"""
DispatchService — 任务分发全生命周期编排（M3/M4）。

所有 SocketServer 回调均在 asyncio 线程中执行。
Tkinter 层通过 socket_server.schedule_coroutine(dispatch_service.dispatch_task(...)) 触发分发。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.domain.models import DispatchRecord, TransferSession
from src.infra.dispatch_repository import DispatchRepository
from src.infra.repositories import TaskRepository
from src.net.protocol.control_message import (
    MsgHelloAck,
    MsgTaskAssign,
    MsgTaskConfirm,
    MsgTaskStatusReport,
    ProcessingParamsPayload,
    ResultRequirements,
    SegmentPayload,
    SyncAction,
    VideoMetaPayload,
)
from src.net.protocol.data_message import (
    MsgChunk,
    MsgChunkAck,
    MsgChunkAckOut,
    MsgResultChunk,
    MsgResultTransferComplete,
    MsgTransferComplete,
    MsgTransferResumeRequest,
)
from src.net.socket.node_session import NodeSession

logger = logging.getLogger(__name__)

# 下载方向分片大小（8MB）
DOWNLOAD_CHUNK_SIZE = 8 * 1024 * 1024
# 等待单片 CHUNK_ACK 超时（秒）
CHUNK_ACK_TIMEOUT = 30.0
# 等待 TASK_CONFIRM 超时（秒）
TASK_CONFIRM_TIMEOUT = 30.0

# dispatch_status 的阶段序，用于防止客户端状态上报将记录回退。
_STATUS_STAGE_ORDER: dict[str, int] = {
    "assigned": 0,
    "confirmed": 1,
    "transferring": 2,
    "running": 3,
    "uploading": 4,
    "done": 5,
    "failed": 6,
    "canceled": 6,
}


def _protocol_log(event: str, current: str, next_step: str, **fields: object) -> None:
    """Structured protocol logs for tracking step progression and next action."""
    extra = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    logger.info("[protocol][%s] current=%s next=%s %s", event, current, next_step, extra)


class DispatchService:
    """任务分发编排器：将 review_done 任务下发给在线节点并接收结果。"""

    def __init__(
        self,
        socket_server,  # SocketServer — 在 bootstrap 中注入，避免循环导入
        dispatch_repo: DispatchRepository,
        task_repo: TaskRepository,
        result_ingest_service,  # ResultIngestService
        results_dir: Path,
    ) -> None:
        self._server = socket_server
        self._dispatch_repo = dispatch_repo
        self._task_repo = task_repo
        self._result_ingest = result_ingest_service
        self._results_dir = results_dir

        # (transfer_id, chunk_index) → asyncio.Event  用于等待下载 CHUNK_ACK
        self._download_ack_events: dict[tuple[str, int], asyncio.Event] = {}

        # task_id_str → asyncio.Event  等待 TASK_CONFIRM
        self._confirm_events: dict[str, asyncio.Event] = {}
        # task_id_str → MsgTaskConfirm  存储收到的确认消息
        self._confirm_msgs: dict[str, MsgTaskConfirm] = {}

    # ── SocketServer 回调（asyncio 线程中调用）───────────────────────────────

    def on_session_ready(self, session: NodeSession) -> None:
        """节点 HELLO 收到、双通道就绪后调用。负责发送 HELLO_ACK。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._handle_hello(session))

    def on_session_closed(self, session: NodeSession) -> None:
        """节点断线时调用。"""
        node_id = session.node_id
        if node_id is None:
            return
        try:
            self._dispatch_repo.update_node_status(node_id, "offline")
            active = self._dispatch_repo.get_active_record_for_node(node_id)
            if active and active.dispatch_status in ("transferring", "running", "uploading"):
                self._dispatch_repo.append_audit_log(
                    node_id,
                    "NODE_DISCONNECTED",
                    f"节点断线，分发记录 {active.id} 保持 {active.dispatch_status} 等待重连",
                    active.id,
                )
        except Exception as exc:
            logger.exception("处理节点断线时异常: %s", exc)

    def on_control_message(self, session: NodeSession, msg: object) -> None:
        """控制通道消息路由（从 asyncio 线程同步调用）。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if isinstance(msg, MsgTaskConfirm):
            loop.create_task(self._handle_task_confirm(session, msg))
        elif isinstance(msg, MsgTaskStatusReport):
            loop.create_task(self._handle_status_report(session, msg))

    def on_data_frame(
        self, session: NodeSession, msg: object, payload: bytes
    ) -> None:
        """数据通道帧路由（从 asyncio 线程同步调用）。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        if isinstance(msg, MsgChunkAck):
            # 触发正在等待此 chunk ACK 的下载协程
            key = (msg.transferId, msg.chunkIndex)
            evt = self._download_ack_events.get(key)
            if evt:
                evt.set()
        elif isinstance(msg, MsgTransferResumeRequest):
            loop.create_task(self._handle_resume_request(session, msg))
        elif isinstance(msg, MsgResultChunk):
            loop.create_task(self._handle_result_chunk(session, msg, payload))
        elif isinstance(msg, MsgResultTransferComplete):
            loop.create_task(self._handle_result_transfer_complete(session, msg))

    # ── 公共 API（供 Tkinter 通过 schedule_coroutine 调用）──────────────────

    async def dispatch_task(self, task_id: int, node_id: str) -> None:
        """将任务下发到指定节点（coroutine，通过 schedule_coroutine 从 Tkinter 调用）。"""
        _protocol_log(
            "dispatch_start",
            "GUI requested dispatch",
            "validate task status and node availability",
            task_id=task_id,
            node_id=node_id,
        )
        # 1. 验证任务状态
        task = self._task_repo.get_task(task_id)
        if task.status != "review_done":
            raise ValueError(
                f"任务 {task_id} 状态为 {task.status!r}，仅 review_done 状态可下发"
            )

        # 2. 验证节点在线
        session = self._find_session(node_id)
        if session is None:
            raise ValueError(f"节点 {node_id!r} 不在线")

        # 3. 计算文件信息
        video_path = Path(task.video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        file_size = video_path.stat().st_size
        total_chunks = max(1, (file_size + DOWNLOAD_CHUNK_SIZE - 1) // DOWNLOAD_CHUNK_SIZE)
        loop = asyncio.get_running_loop()
        file_hash = await loop.run_in_executor(None, _sha256_file, video_path)
        transfer_id = str(uuid.uuid4())

        # 4. 构建 interesting 分段列表
        all_segments = self._task_repo.list_segments(task_id, include_labeled=True)
        seg_payloads = [
            SegmentPayload(
                startMs=int(s.start_sec * 1000),
                endMs=int(s.end_sec * 1000),
                label="interesting",
            )
            for s in all_segments
            if s.current_label == "interesting"
        ]

        # 5. 创建分发记录 + 传输会话
        record = self._dispatch_repo.create_dispatch_record(task_id, node_id)
        xfer = self._dispatch_repo.create_transfer_session(
            dispatch_record_id=record.id,
            transfer_id=transfer_id,
            direction="download",
            file_role="video",
            total_chunks=total_chunks,
            file_hash=file_hash,
            file_size_bytes=file_size,
        )

        # 6. 更新节点 DB 状态
        self._dispatch_repo.upsert_node(
            node_id, session.peer_ip, _caps_json(session), "busy"
        )
        self._dispatch_repo.update_node_status(node_id, "busy", record.id)
        session.active_dispatch_id = record.id
        session.status = "busy"

        self._dispatch_repo.append_audit_log(
            node_id,
            "TASK_ASSIGNED",
            f"任务 {task_id} 下发至节点 {node_id}，transfer_id={transfer_id}",
            record.id,
        )
        _protocol_log(
            "dispatch_prepared",
            "dispatch record persisted",
            "send TASK_ASSIGN then wait TASK_CONFIRM",
            task_id=task_id,
            node_id=node_id,
            transfer_id=transfer_id,
            total_chunks=total_chunks,
        )

        # 7. 发送 TASK_ASSIGN（先注册 confirm_event 再发送，防竞态）
        task_id_str = str(task_id)
        confirm_evt = asyncio.Event()
        self._confirm_events[task_id_str] = confirm_evt

        assign_msg = MsgTaskAssign(
            requestId=str(uuid.uuid4()),
            taskId=task_id_str,
            videoMeta=VideoMetaPayload(
                videoName=task.video_name,
                fileSizeBytes=file_size,
                totalChunks=total_chunks,
                fileHash=file_hash,
            ),
            processingParams=ProcessingParamsPayload(
                segments=seg_payloads,
                codecHint="hevc",
                targetBitrateKbps=0,
            ),
            resultRequirements=ResultRequirements(
                includeResultJson=True,
                includeLog=True,
            ),
        )

        try:
            await session.send_control(assign_msg.to_dict())
            logger.info("TASK_ASSIGN 已发送: task=%d node=%s", task_id, node_id)
            _protocol_log(
                "task_assign_sent",
                "TASK_ASSIGN sent",
                "wait TASK_CONFIRM from node",
                task_id=task_id,
                node_id=node_id,
                confirm_timeout_sec=TASK_CONFIRM_TIMEOUT,
            )

            # 8. 等待 TASK_CONFIRM
            try:
                await asyncio.wait_for(confirm_evt.wait(), timeout=TASK_CONFIRM_TIMEOUT)
            except asyncio.TimeoutError:
                self._dispatch_repo.update_dispatch_status(
                    record.id, "failed", "等待 TASK_CONFIRM 超时"
                )
                self._dispatch_repo.append_audit_log(
                    node_id, "CONFIRM_TIMEOUT", "等待 TASK_CONFIRM 超时 (30s)", record.id
                )
                _protocol_log(
                    "task_confirm_timeout",
                    "wait TASK_CONFIRM timeout",
                    "node should reconnect and server can re-dispatch",
                    task_id=task_id,
                    node_id=node_id,
                )
                return

            confirm_msg = self._confirm_msgs.pop(task_id_str, None)
            if confirm_msg is None or not confirm_msg.accepted:
                reason = confirm_msg.reason if confirm_msg else "未知原因"
                self._dispatch_repo.update_dispatch_status(
                    record.id, "failed", f"节点拒绝: {reason}"
                )
                self._dispatch_repo.append_audit_log(
                    node_id, "TASK_REJECTED", f"节点拒绝: {reason}", record.id
                )
                _protocol_log(
                    "task_rejected",
                    "TASK_CONFIRM accepted=false",
                    "wait manual retry or dispatch to another node",
                    task_id=task_id,
                    node_id=node_id,
                    reason=reason,
                )
                return

            # 9. 节点接受 → 开始传输
            self._dispatch_repo.update_dispatch_status(record.id, "confirmed")
            logger.info("节点确认接受任务: task=%d node=%s", task_id, node_id)
            _protocol_log(
                "task_confirmed",
                "TASK_CONFIRM accepted=true",
                "start file chunk transfer on data channel",
                task_id=task_id,
                node_id=node_id,
                transfer_id=transfer_id,
            )
            await self._do_download_transfer(session, record, xfer, video_path)

        finally:
            self._confirm_events.pop(task_id_str, None)

    # ── 内部：HELLO 处理 ──────────────────────────────────────────────────────

    async def _handle_hello(self, session: NodeSession) -> None:
        """处理节点上线 HELLO，注册节点，发送 HELLO_ACK（含 sync_actions）。"""
        node_id = session.node_id
        if node_id is None:
            return
        _protocol_log(
            "hello_handle_start",
            "session ready callback entered",
            "persist node and send HELLO_ACK",
            node_id=node_id,
            peer_ip=session.peer_ip,
        )

        # 注册/更新节点
        self._dispatch_repo.upsert_node(
            node_id, session.peer_ip, _caps_json(session), "online"
        )
        self._dispatch_repo.append_audit_log(
            node_id,
            "NODE_ONLINE",
            f"ip={session.peer_ip} version={session.node_version}",
        )

        # 构建 sync_actions（断线恢复）
        sync_actions: list[SyncAction] = []
        active = self._dispatch_repo.get_active_record_for_node(node_id)
        if active:
            task_id_str = str(active.task_id)
            if active.dispatch_status == "uploading":
                sync_actions.append(SyncAction("RESUME_UPLOAD", task_id_str))
                logger.info("节点重连，请求恢复上传: task=%d", active.task_id)
            elif active.dispatch_status in ("running", "transferring"):
                sync_actions.append(SyncAction("QUERY_PROGRESS", task_id_str))
                logger.info("节点重连，查询进度: task=%d", active.task_id)

        ack = MsgHelloAck(
            requestId=str(uuid.uuid4()),
            serverTime=datetime.now(timezone.utc).isoformat(),
            syncActions=sync_actions,
        )
        try:
            await session.send_control(ack.to_dict())
            logger.info(
                "HELLO_ACK 已发送: node=%s sync_actions=%d", node_id, len(sync_actions)
            )
            _protocol_log(
                "hello_ack_sent",
                "HELLO_ACK sent",
                "wait TASK_ASSIGN or status reporting",
                node_id=node_id,
                sync_actions=len(sync_actions),
            )
        except Exception as exc:
            logger.exception("发送 HELLO_ACK 失败: %s", exc)

    # ── 内部：TASK_CONFIRM 处理 ───────────────────────────────────────────────

    async def _handle_task_confirm(
        self, session: NodeSession, msg: MsgTaskConfirm
    ) -> None:
        task_id_str = msg.taskId
        self._confirm_msgs[task_id_str] = msg
        _protocol_log(
            "task_confirm_received",
            "TASK_CONFIRM received",
            "wake dispatch coroutine and continue transfer/abort",
            node_id=session.node_id,
            task_id=task_id_str,
            accepted=msg.accepted,
        )
        evt = self._confirm_events.get(task_id_str)
        if evt:
            evt.set()
        else:
            logger.warning("收到 TASK_CONFIRM 但无等待协程: task_id=%s", task_id_str)

    # ── 内部：TASK_STATUS_REPORT 处理 ────────────────────────────────────────

    async def _handle_status_report(
        self, session: NodeSession, msg: MsgTaskStatusReport
    ) -> None:
        try:
            task_id = int(msg.taskId)
        except ValueError:
            return

        node_id = session.node_id or ""
        record = self._dispatch_repo.get_active_record_for_node(node_id)
        if record is None or record.task_id != task_id:
            return

        # Android TaskState 名称 → dispatch_status 映射
        STATUS_MAP: dict[str, str] = {
            # 兼容旧客户端：连接已建立但仍上报 Connecting 时，按 confirmed 处理。
            "Connecting": "confirmed",
            "Receiving": "transferring",
            "AwaitingTask": "confirmed",
            "Processing": "running",
            "Uploading": "uploading",
            "Done": "done",
            "Error": "failed",
        }
        new_status = STATUS_MAP.get(msg.status)
        if new_status and new_status != record.dispatch_status:
            # 仅允许推进，不允许回退（例如 running 被错误上报为 AwaitingTask/Connecting）。
            cur_stage = _STATUS_STAGE_ORDER.get(record.dispatch_status, -1)
            next_stage = _STATUS_STAGE_ORDER.get(new_status, -1)
            if next_stage < cur_stage:
                logger.warning(
                    "忽略状态回退: task=%d current=%s reported=%s",
                    task_id,
                    record.dispatch_status,
                    msg.status,
                )
                return
            self._dispatch_repo.update_dispatch_status(
                record.id, new_status, msg.lastError
            )
            logger.info(
                "分发状态更新: task=%d %s→%s (%.0f%%)",
                task_id, record.dispatch_status, new_status, msg.progress * 100,
            )

    # ── 内部：下载传输 ────────────────────────────────────────────────────────

    async def _do_download_transfer(
        self,
        session: NodeSession,
        record: DispatchRecord,
        xfer: TransferSession,
        video_path: Path,
    ) -> None:
        """逐片发送视频文件，等待每片 CHUNK_ACK（stop-and-wait）。"""
        self._dispatch_repo.update_dispatch_status(record.id, "transferring")
        task_id_str = str(record.task_id)
        transfer_id = xfer.transfer_id
        loop = asyncio.get_running_loop()
        _protocol_log(
            "download_start",
            "dispatch confirmed",
            "send CHUNK frames and await CHUNK_ACK",
            task_id=record.task_id,
            node_id=session.node_id,
            transfer_id=transfer_id,
            total_chunks=xfer.total_chunks,
        )

        try:
            with open(video_path, "rb") as f:
                chunk_index = 0
                while True:
                    chunk = await loop.run_in_executor(None, f.read, DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break

                    chunk_hash = hashlib.sha256(chunk).hexdigest()
                    key = (transfer_id, chunk_index)

                    # 先注册 ACK Event，再发送——防止快速 ACK 丢失（镜像 Android UNDISPATCHED 模式）
                    ack_evt = asyncio.Event()
                    self._download_ack_events[key] = ack_evt
                    try:
                        await session.send_data_frame(
                            MsgChunk(
                                taskId=task_id_str,
                                transferId=transfer_id,
                                chunkIndex=chunk_index,
                                chunkHash=chunk_hash,
                                payloadSize=len(chunk),
                            ).to_dict(),
                            chunk,
                        )
                        try:
                            await asyncio.wait_for(ack_evt.wait(), timeout=CHUNK_ACK_TIMEOUT)
                        except asyncio.TimeoutError:
                            raise IOError(
                                f"等待 CHUNK_ACK 超时: chunk={chunk_index}"
                            )
                        self._dispatch_repo.mark_chunk_acked(xfer.id, chunk_index)
                        logger.debug(
                            "[task=%d] chunk %d/%d ACK",
                            record.task_id, chunk_index + 1, xfer.total_chunks,
                        )
                    finally:
                        self._download_ack_events.pop(key, None)

                    chunk_index += 1

            # 发送 TRANSFER_COMPLETE
            await session.send_data_frame(
                MsgTransferComplete(
                    taskId=task_id_str,
                    transferId=transfer_id,
                    totalHash=xfer.file_hash,
                ).to_dict()
            )
            self._dispatch_repo.complete_transfer_session(xfer.id)
            self._dispatch_repo.update_dispatch_status(record.id, "running")
            logger.info(
                "下载传输完成: task=%d chunks=%d hash=%s...",
                record.task_id, xfer.total_chunks, xfer.file_hash[:8],
            )
            _protocol_log(
                "download_complete",
                "TRANSFER_COMPLETE sent",
                "wait node processing then result upload",
                task_id=record.task_id,
                node_id=session.node_id,
                transfer_id=transfer_id,
            )
            self._dispatch_repo.append_audit_log(
                session.node_id or "",
                "DOWNLOAD_COMPLETE",
                f"chunks={xfer.total_chunks} hash={xfer.file_hash[:8]}...",
                record.id,
            )

        except Exception as exc:
            logger.exception("下载传输失败: task=%d: %s", record.task_id, exc)
            self._dispatch_repo.update_dispatch_status(record.id, "failed", str(exc))
            self._dispatch_repo.append_audit_log(
                session.node_id or "", "DOWNLOAD_FAILED", str(exc), record.id
            )

    # ── 内部：断点续传 ────────────────────────────────────────────────────────

    async def _handle_resume_request(
        self, session: NodeSession, msg: MsgTransferResumeRequest
    ) -> None:
        """收到节点 TRANSFER_RESUME_REQUEST，仅补发缺失分片。"""
        _protocol_log(
            "resume_request_received",
            "TRANSFER_RESUME_REQUEST received",
            "calculate missing chunks and retransmit",
            node_id=session.node_id,
            transfer_id=msg.transferId,
            missing_count=len(msg.missingIndices),
        )
        xfer = self._dispatch_repo.get_transfer_session_by_transfer_id(msg.transferId)
        if xfer is None:
            logger.warning("续传请求：找不到 transfer_id=%s", msg.transferId)
            return

        record = self._dispatch_repo.get_dispatch_record(xfer.dispatch_record_id)
        if record is None:
            return

        try:
            task = self._task_repo.get_task(record.task_id)
        except ValueError:
            return

        video_path = Path(task.video_path)
        if not video_path.exists():
            logger.error("续传失败：视频文件不存在: %s", video_path)
            return

        task_id_str = str(record.task_id)
        transfer_id = msg.transferId
        loop = asyncio.get_running_loop()

        # 以 DB 未 ACK 分片为准，并入节点请求的缺失列表
        db_missing = set(self._dispatch_repo.get_missing_chunk_indices(xfer.id))
        node_missing = set(msg.missingIndices)
        missing = sorted(db_missing | node_missing)

        logger.info("续传: task=%d missing=%d chunks", record.task_id, len(missing))
        self._dispatch_repo.update_dispatch_status(record.id, "transferring")
        self._dispatch_repo.append_audit_log(
            session.node_id or "",
            "TRANSFER_RESUME",
            f"缺失 {len(missing)} 片 indices={missing[:10]}...",
            record.id,
        )

        try:
            with open(video_path, "rb") as f:
                for chunk_index in missing:
                    offset = chunk_index * DOWNLOAD_CHUNK_SIZE
                    f.seek(offset)
                    chunk = await loop.run_in_executor(None, f.read, DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        continue

                    chunk_hash = hashlib.sha256(chunk).hexdigest()
                    key = (transfer_id, chunk_index)
                    ack_evt = asyncio.Event()
                    self._download_ack_events[key] = ack_evt
                    try:
                        await session.send_data_frame(
                            MsgChunk(
                                taskId=task_id_str,
                                transferId=transfer_id,
                                chunkIndex=chunk_index,
                                chunkHash=chunk_hash,
                                payloadSize=len(chunk),
                            ).to_dict(),
                            chunk,
                        )
                        await asyncio.wait_for(ack_evt.wait(), timeout=CHUNK_ACK_TIMEOUT)
                        self._dispatch_repo.mark_chunk_acked(xfer.id, chunk_index)
                    except asyncio.TimeoutError:
                        raise IOError(f"续传 ACK 超时: chunk={chunk_index}")
                    finally:
                        self._download_ack_events.pop(key, None)

            # 全部补发完毕，重发 TRANSFER_COMPLETE
            await session.send_data_frame(
                MsgTransferComplete(
                    taskId=task_id_str,
                    transferId=transfer_id,
                    totalHash=xfer.file_hash,
                ).to_dict()
            )
            self._dispatch_repo.complete_transfer_session(xfer.id)
            self._dispatch_repo.update_dispatch_status(record.id, "running")
            logger.info("续传完成: task=%d", record.task_id)
            _protocol_log(
                "resume_complete",
                "missing chunks retransmitted",
                "wait node processing then result upload",
                task_id=record.task_id,
                node_id=session.node_id,
                transfer_id=transfer_id,
            )

        except Exception as exc:
            logger.exception("续传失败: %s", exc)
            self._dispatch_repo.update_dispatch_status(record.id, "failed", str(exc))

    # ── 内部：结果分片接收 ────────────────────────────────────────────────────

    async def _handle_result_chunk(
        self, session: NodeSession, msg: MsgResultChunk, payload: bytes
    ) -> None:
        """接收结果分片，校验 hash，落盘，发送 CHUNK_ACK。"""
        logger.info(
            "recv RESULT_CHUNK taskId=%s transferId=%s chunkIndex=%d fileRole=%s payloadSize=%d",
            msg.taskId,
            msg.transferId,
            msg.chunkIndex,
            msg.fileRole,
            msg.payloadSize,
        )

        if msg.payloadSize != len(payload):
            logger.warning(
                "drop message reason=invalid_field type=RESULT_CHUNK taskId=%s transferId=%s "
                "chunkIndex=%d expectedPayloadSize=%d actualPayloadSize=%d",
                msg.taskId,
                msg.transferId,
                msg.chunkIndex,
                msg.payloadSize,
                len(payload),
            )
            return

        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != msg.chunkHash:
            logger.warning(
                "drop message reason=invalid_field type=RESULT_CHUNK taskId=%s transferId=%s "
                "chunkIndex=%d fileRole=%s expectedHash=%s actualHash=%s",
                msg.taskId,
                msg.transferId,
                msg.chunkIndex,
                msg.fileRole,
                msg.chunkHash,
                actual_hash,
            )
            return  # 不 ACK，等节点超时重传

        chunk_dir = (
            self._results_dir
            / msg.taskId
            / (session.node_id or "unknown")
            / "chunks"
            / msg.transferId
            / msg.fileRole
        )

        # 首个有效结果分片到达即进入 uploading，避免状态长期停留在 running/confirmed。
        if msg.chunkIndex == 0 and session.node_id:
            record = self._dispatch_repo.get_active_record_for_node(session.node_id)
            if record and record.dispatch_status in ("running", "confirmed", "transferring"):
                self._dispatch_repo.update_dispatch_status(record.id, "uploading")

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, _write_chunk, chunk_dir, msg.chunkIndex, payload)
        except Exception as exc:
            logger.exception(
                "drop message reason=write_error type=RESULT_CHUNK taskId=%s transferId=%s "
                "chunkIndex=%d fileRole=%s error=%s",
                msg.taskId,
                msg.transferId,
                msg.chunkIndex,
                msg.fileRole,
                exc,
            )
            return

        try:
            await session.send_data_frame(
                MsgChunkAckOut(
                    taskId=msg.taskId,
                    transferId=msg.transferId,
                    chunkIndex=msg.chunkIndex,
                ).to_dict()
            )
            logger.info(
                "send CHUNK_ACK taskId=%s transferId=%s chunkIndex=%d",
                msg.taskId,
                msg.transferId,
                msg.chunkIndex,
            )
            if msg.chunkIndex == 0:
                _protocol_log(
                    "result_upload_started",
                    "first result chunk acknowledged",
                    "continue receiving RESULT_CHUNK until RESULT_TRANSFER_COMPLETE",
                    task_id=msg.taskId,
                    node_id=session.node_id,
                    transfer_id=msg.transferId,
                    file_role=msg.fileRole,
                )
        except Exception as exc:
            logger.exception("发送结果 CHUNK_ACK 失败: %s", exc)

    # ── 内部：结果传输完成 ────────────────────────────────────────────────────

    async def _handle_result_transfer_complete(
        self, session: NodeSession, msg: MsgResultTransferComplete
    ) -> None:
        """触发结果文件组装与验收。"""
        _protocol_log(
            "result_transfer_complete_received",
            "RESULT_TRANSFER_COMPLETE received",
            "ingest files, verify hash, update dispatch status",
            task_id=msg.taskId,
            node_id=session.node_id,
            transfer_id=msg.transferId,
        )
        try:
            task_id = int(msg.taskId)
        except ValueError:
            logger.error("无效 taskId: %s", msg.taskId)
            return

        node_id = session.node_id or "unknown"
        chunks_base = (
            self._results_dir / msg.taskId / node_id / "chunks" / msg.transferId
        )
        out_dir = self._results_dir / msg.taskId / node_id

        record = self._dispatch_repo.get_active_record_for_node(node_id)
        if record is None:
            logger.warning("结果完成但无活跃分发记录: task=%d node=%s", task_id, node_id)
            return

        self._dispatch_repo.update_dispatch_status(record.id, "uploading")

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                self._result_ingest.ingest,
                task_id,
                node_id,
                record.id,
                chunks_base,
                out_dir,
                msg.totalHash,
            )
            self._dispatch_repo.update_dispatch_status(record.id, "done")
            self._dispatch_repo.update_node_status(node_id, "online", None)
            session.active_dispatch_id = None
            session.status = "online"
            logger.info("结果验收通过: task=%d node=%s", task_id, node_id)
            _protocol_log(
                "result_accepted",
                "result ingest completed",
                "node returns to online idle and waits next TASK_ASSIGN",
                task_id=task_id,
                node_id=node_id,
            )
            self._dispatch_repo.append_audit_log(
                node_id,
                "RESULT_ACCEPTED",
                f"task={task_id} hash={msg.totalHash[:8]}...",
                record.id,
            )
        except Exception as exc:
            logger.exception("结果验收失败: task=%d: %s", task_id, exc)
            self._dispatch_repo.update_dispatch_status(record.id, "failed", str(exc))
            self._dispatch_repo.append_audit_log(
                node_id, "RESULT_REJECTED", str(exc), record.id
            )

    # ── 供 GUI 轮询的查询 API ─────────────────────────────────────────────────

    def list_online_nodes(self) -> list[dict]:
        """返回当前在线节点快照（供 Tkinter 5 秒轮询）。"""
        return [
            {
                "node_id": s.node_id or "",
                "ip": s.peer_ip,
                "status": s.status,
                "dispatch_id": s.active_dispatch_id,
                "last_seen": s.last_seen_at.isoformat() if s.last_seen_at else "",
            }
            for s in self._server.list_ready_sessions()
        ]

    def list_dispatch_records(self, task_id: int):
        return self._dispatch_repo.list_records_for_task(task_id)

    # ── 工具 ──────────────────────────────────────────────────────────────────

    def _find_session(self, node_id: str) -> Optional[NodeSession]:
        for s in self._server.list_ready_sessions():
            if s.node_id == node_id:
                return s
        return None


# ── 模块级工具函数 ────────────────────────────────────────────────────────────

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(8 * 1024 * 1024)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _caps_json(session: NodeSession) -> str:
    if session.capabilities is None:
        return "{}"
    return json.dumps(session.capabilities.to_dict(), ensure_ascii=False)


def _write_chunk(chunk_dir: Path, chunk_index: int, payload: bytes) -> None:
    chunk_dir.mkdir(parents=True, exist_ok=True)
    (chunk_dir / f"{chunk_index:08d}.bin").write_bytes(payload)

