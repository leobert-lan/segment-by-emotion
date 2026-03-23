"""
tests/test_dispatch_service.py — M2/M3/M4 分发服务逻辑测试。

使用 mock SocketServer 和临时 SQLite DB，不触碰真实网络。
"""
import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from src.infra.db import Database
from src.infra.dispatch_repository import DispatchRepository
from src.infra.repositories import TaskRepository
from src.services.dispatch_service import DispatchService, DOWNLOAD_CHUNK_SIZE
from src.services.heat_service import HeatAnalyzer
from src.services.ingest_service import TaskIngestService
from src.services.result_ingest_service import ResultIngestService
from src.net.protocol.control_message import MsgTaskConfirm
from src.net.protocol.data_message import (
    MsgChunkAck,
    MsgTransferResumeRequest,
    MsgResultChunk,
    MsgResultTransferComplete,
)
from src.net.socket.node_session import NodeSession

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "src" / "infra" / "schema.sql"


def _make_db(tmp_dir: Path) -> Database:
    db = Database(tmp_dir / "test.db")
    db.initialize(SCHEMA_PATH)
    return db


def _make_dummy_session(node_id: str = "test-node", ip: str = "192.168.1.2") -> NodeSession:
    session = NodeSession(ip)
    session.node_id = node_id
    session.node_version = "1.0.0"
    # ctrl_writer mock
    session.ctrl_writer = MagicMock()
    session.ctrl_writer.is_closing.return_value = False
    session.ctrl_writer.write = MagicMock()
    session.ctrl_writer.drain = AsyncMock()
    # data_writer mock
    session.data_writer = MagicMock()
    session.data_writer.is_closing.return_value = False
    session.data_writer.write = MagicMock()
    session.data_writer.drain = AsyncMock()
    session._data_ready.set()
    session.status = "online"
    return session


class MockSocketServer:
    """最小化 mock SocketServer，记录发送调用。"""
    def __init__(self, sessions: list[NodeSession]):
        self._sessions = sessions
        self.sent_control: list[tuple[str, dict]] = []
        self.sent_data: list[tuple[str, dict, bytes]] = []
        self._loop = asyncio.get_event_loop()

    def list_ready_sessions(self):
        return self._sessions

    def schedule_coroutine(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)


class TestDispatchService(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        db = _make_db(self.tmp)
        self.task_repo = TaskRepository(db)
        self.dispatch_repo = DispatchRepository(db)
        self.result_ingest = ResultIngestService()
        self.results_dir = self.tmp / "results"
        self.results_dir.mkdir()

        # 创建一个 review_done 任务
        analyzer = HeatAnalyzer()
        analyzer._try_load_audio = lambda _path: (None, None)  # type: ignore[method-assign]
        ingest = TaskIngestService(self.task_repo, analyzer)

        video = self.tmp / "sample.mp4"
        video.write_bytes(b"\x00" * (DOWNLOAD_CHUNK_SIZE * 3 + 500))  # 3 full + 1 tail chunk

        self.task = ingest.create_task_and_run_stage1(str(video), "spk-001", segment_duration=2.0)
        self.task_repo.update_task_status(self.task.id, "review_done")
        self.video_path = video

        self.session = _make_dummy_session()
        self.mock_server = MockSocketServer([self.session])

        self.svc = DispatchService(
            socket_server=self.mock_server,
            dispatch_repo=self.dispatch_repo,
            task_repo=self.task_repo,
            result_ingest_service=self.result_ingest,
            results_dir=self.results_dir,
        )

    async def asyncTearDown(self) -> None:
        self._tmpdir.cleanup()

    # ── 测试 1：dispatch_task 非 review_done 任务应抛异常 ─────────────────────

    async def test_dispatch_non_review_done_raises(self) -> None:
        self.task_repo.update_task_status(self.task.id, "stage1_done")
        with self.assertRaises(ValueError, msg="非 review_done 应拒绝"):
            await self.svc.dispatch_task(self.task.id, "test-node")

    # ── 测试 2：节点不在线应抛异常 ────────────────────────────────────────────

    async def test_dispatch_offline_node_raises(self) -> None:
        with self.assertRaises(ValueError, msg="不在线节点应拒绝"):
            await self.svc.dispatch_task(self.task.id, "offline-node")

    # ── 测试 3：dispatch_task 成功路径（TASK_ASSIGN 已发，等到 TASK_CONFIRM） ──

    async def test_dispatch_creates_record_and_sends_assign(self) -> None:
        """
        拦截 session.send_control：TASK_ASSIGN 发出的瞬间立即注入 TASK_CONFIRM。
        同时用后台任务持续触发所有待 ACK 的下载分片事件，确保传输循环完成。
        """
        sent_controls: list[dict] = []

        async def intercept_send_control(msg_dict: dict) -> None:
            sent_controls.append(msg_dict)
            if msg_dict.get("type") == "TASK_ASSIGN":
                # TASK_ASSIGN 发出后，立即模拟节点 TASK_CONFIRM
                confirm = MsgTaskConfirm(
                    requestId="req-confirm",
                    taskId=msg_dict["taskId"],
                    accepted=True,
                )
                await self.svc._handle_task_confirm(self.session, confirm)

        self.session.send_control = intercept_send_control  # type: ignore[assignment]

        # 后台任务：持续触发下载 ACK，防止传输循环阻塞
        async def auto_ack_chunks() -> None:
            while True:
                await asyncio.sleep(0.001)
                for key, evt in list(self.svc._download_ack_events.items()):
                    if not evt.is_set():
                        evt.set()

        ack_task = asyncio.create_task(auto_ack_chunks())
        try:
            await asyncio.wait_for(
                self.svc.dispatch_task(self.task.id, "test-node"),
                timeout=10.0,
            )
        finally:
            ack_task.cancel()

        # 验证 TASK_ASSIGN 已发送且字段正确
        assign_msgs = [m for m in sent_controls if m.get("type") == "TASK_ASSIGN"]
        self.assertEqual(len(assign_msgs), 1, f"sent_controls={sent_controls}")
        self.assertEqual(assign_msgs[0]["taskId"], str(self.task.id))
        self.assertEqual(assign_msgs[0]["videoMeta"]["totalChunks"], 4)

        # 验证 dispatch_record 已创建
        records = self.dispatch_repo.list_records_for_task(self.task.id)
        self.assertGreater(len(records), 0)
        self.assertIn(
            records[0].dispatch_status,
            ("running", "confirmed", "transferring", "done"),
        )

    # ── 测试 4：on_session_ready → HELLO_ACK 发送 ─────────────────────────────

    async def test_hello_ack_sent_on_session_ready(self) -> None:
        sent_dicts: list[dict] = []

        async def capture_send(d):
            sent_dicts.append(d)

        self.session.send_control = capture_send  # type: ignore[assignment]
        self.svc.on_session_ready(self.session)
        # 让 event loop 执行一次
        await asyncio.sleep(0.05)
        self.assertTrue(any(d.get("type") == "HELLO_ACK" for d in sent_dicts),
                        f"未收到 HELLO_ACK，sent={sent_dicts}")

    # ── 测试 5：TRANSFER_RESUME_REQUEST 仅补发缺失分片 ────────────────────────

    async def test_resume_request_resends_missing_only(self) -> None:
        # 手动创建 transfer session，假设 chunk 0 和 2 未 ACK
        record = self.dispatch_repo.create_dispatch_record(self.task.id, "test-node")
        file_hash = hashlib.sha256(self.video_path.read_bytes()).hexdigest()
        xfer = self.dispatch_repo.create_transfer_session(
            dispatch_record_id=record.id,
            transfer_id="tid-resume",
            direction="download",
            file_role="video",
            total_chunks=4,
            file_hash=file_hash,
            file_size_bytes=self.video_path.stat().st_size,
        )
        # chunk 1 已 ACK，0 和 2 未 ACK
        self.dispatch_repo.mark_chunk_acked(xfer.id, 1)

        sent_chunks: list[int] = []

        async def capture_data(header_dict, payload=b""):
            if header_dict.get("type") == "CHUNK":
                sent_chunks.append(header_dict["chunkIndex"])

        self.session.send_data_frame = capture_data  # type: ignore[assignment]

        resume_msg = MsgTransferResumeRequest(
            taskId=str(self.task.id),
            transferId="tid-resume",
            missingIndices=[0, 2],
        )

        # 在另一个 task 中注入 ACK，避免死锁
        async def inject_acks():
            for i in range(10):
                await asyncio.sleep(0.01)
                for idx in [0, 2, 3]:
                    key = ("tid-resume", idx)
                    evt = self.svc._download_ack_events.get(key)
                    if evt:
                        evt.set()

        ack_task = asyncio.create_task(inject_acks())
        await asyncio.wait_for(
            asyncio.gather(
                self.svc._handle_resume_request(self.session, resume_msg),
                ack_task,
                return_exceptions=True,
            ),
            timeout=5.0,
        )

        # 只发送了缺失的分片（0 和 2），不包含已 ACK 的 1
        self.assertIn(0, sent_chunks)
        self.assertIn(2, sent_chunks)
        self.assertNotIn(1, sent_chunks)

    # ── 测试 6：RESULT_CHUNK hash 不匹配不发 ACK ──────────────────────────────

    async def test_result_chunk_bad_hash_no_ack(self) -> None:
        acks_sent: list = []

        async def capture_data(header_dict, payload=b""):
            if header_dict.get("type") == "CHUNK_ACK":
                acks_sent.append(header_dict)

        self.session.send_data_frame = capture_data  # type: ignore[assignment]

        bad_msg = MsgResultChunk(
            taskId=str(self.task.id),
            transferId="tid-upload",
            chunkIndex=0,
            chunkHash="wrong_hash",
            payloadSize=4,
            fileRole="video",
        )
        await self.svc._handle_result_chunk(self.session, bad_msg, b"\x01\x02\x03\x04")
        self.assertEqual(len(acks_sent), 0, "hash 不匹配时不应发送 CHUNK_ACK")

    # ── 测试 7：RESULT_CHUNK 正确时发送 ACK ───────────────────────────────────

    async def test_result_chunk_good_hash_sends_ack(self) -> None:
        payload = b"\xDE\xAD\xBE\xEF"
        correct_hash = hashlib.sha256(payload).hexdigest()
        acks_sent: list = []

        async def capture_data(header_dict, data=b""):
            if header_dict.get("type") == "CHUNK_ACK":
                acks_sent.append(header_dict)

        self.session.send_data_frame = capture_data  # type: ignore[assignment]

        msg = MsgResultChunk(
            taskId=str(self.task.id),
            transferId="tid-upload-2",
            chunkIndex=0,
            chunkHash=correct_hash,
            payloadSize=len(payload),
            fileRole="video",
        )
        await self.svc._handle_result_chunk(self.session, msg, payload)
        self.assertEqual(len(acks_sent), 1)
        self.assertEqual(acks_sent[0]["chunkIndex"], 0)


if __name__ == "__main__":
    unittest.main()

