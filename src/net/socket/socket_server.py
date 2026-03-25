"""
SocketServer — 双通道 asyncio TCP 服务端，在后台守护线程中运行。

控制通道 :23010 — 换行符分隔 JSON
数据通道 :23011 — [4B header-len][JSON header][binary payload]

通道配对策略：以 peer_ip 为 key，先到达的通道暂存于 pending dict；
同一 IP 的第二条连接到达时立即配对。配对等待上限 30 秒。

线程安全 API（供 Tkinter 线程调用）：
  send_control(node_id, msg_dict)
  send_data_frame(node_id, header_dict, payload=b"")
  list_ready_sessions() → list[NodeSession]
  stop()
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from src.net.socket.node_session import NodeSession
from src.net.protocol.control_message import MsgHello, decode_control
from src.net.protocol.message_framer import read_data_frame

logger = logging.getLogger(__name__)

# 等待另一通道连接的超时（秒）
_PAIR_TIMEOUT = 30.0
# 等待 HELLO 消息的超时（秒）
_HELLO_TIMEOUT = 30.0
# 数据通道先到达时，等待控制通道的超时（秒）
_PENDING_DATA_TIMEOUT = 30.0
_HEARTBEAT_INTERVAL = 5.0
_HEARTBEAT_TIMEOUT = 45.0


def _protocol_log(event: str, current: str, next_step: str, **fields: Any) -> None:
    """Structured protocol logs that explicitly describe next expected step."""
    extra = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    logger.info("[protocol][%s] current=%s next=%s %s", event, current, next_step, extra)


class SocketServer:
    """双端口 asyncio TCP 服务端（控制 + 数据）。"""

    def __init__(
        self,
        host: str,
        control_port: int,
        data_port: int,
        on_session_ready: Callable[[NodeSession], None] | None = None,
        on_session_closed: Callable[[NodeSession], None] | None = None,
        on_control_message: Callable[[NodeSession, Any], None] | None = None,
        on_data_frame: Callable[[NodeSession, Any, bytes], None] | None = None,
    ) -> None:
        self._host = host
        self._control_port = control_port
        self._data_port = data_port

        # 回调（均从 asyncio 线程调用）
        self._on_session_ready = on_session_ready
        self._on_session_closed = on_session_closed
        self._on_control_message = on_control_message
        self._on_data_frame = on_data_frame

        # 已就绪 session: node_id → NodeSession
        self._active: dict[str, NodeSession] = {}

        # 等待配对的控制通道: peer_ip → NodeSession
        self._pending_ctrl: dict[str, NodeSession] = {}
        # 等待配对的数据通道: peer_ip → (reader, writer)
        self._pending_data: dict[str, tuple] = {}
        # 数据通道等待控制通道的事件: peer_ip → asyncio.Event
        self._pending_data_events: dict[str, asyncio.Event] = {}
        # 配对事件: peer_ip → asyncio.Event
        self._pair_events: dict[str, asyncio.Event] = {}

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._watchdog_task: Optional[asyncio.Task[None]] = None

    # ── 启动 / 停止 ───────────────────────────────────────────────────────────

    def start_in_thread(self) -> None:
        """在守护线程中启动 asyncio 事件循环和双端口服务。"""
        ready = threading.Event()

        def _run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._stop_event = asyncio.Event()
            ready.set()
            try:
                self._loop.run_until_complete(self._serve())
            finally:
                self._loop.close()

        self._thread = threading.Thread(target=_run, name="SocketServer", daemon=True)
        self._thread.start()
        ready.wait(timeout=5)
        logger.info(
            "SocketServer 已启动: ctrl=%s:%d data=%s:%d",
            self._host, self._control_port, self._host, self._data_port,
        )

    def stop(self) -> None:
        if self._loop and self._stop_event:
            asyncio.run_coroutine_threadsafe(self._signal_stop(), self._loop)
        if self._thread:
            self._thread.join(timeout=5)

    # ── 线程安全公共 API ──────────────────────────────────────────────────────

    def send_control(self, node_id: str, msg_dict: dict) -> None:
        """线程安全：从任意线程向节点发送控制消息。"""
        if self._loop is None:
            raise RuntimeError("SocketServer 尚未启动")
        asyncio.run_coroutine_threadsafe(
            self._send_control_async(node_id, msg_dict), self._loop
        )

    def send_data_frame(
        self, node_id: str, header_dict: dict, payload: bytes = b""
    ) -> None:
        """线程安全：从任意线程向节点发送数据帧。"""
        if self._loop is None:
            raise RuntimeError("SocketServer 尚未启动")
        asyncio.run_coroutine_threadsafe(
            self._send_data_async(node_id, header_dict, payload), self._loop
        )

    def schedule_coroutine(self, coro) -> "asyncio.Future[Any]":
        """线程安全：在服务端事件循环中调度协程，返回 Future。"""
        if self._loop is None:
            raise RuntimeError("SocketServer 尚未启动")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def list_ready_sessions(self) -> list[NodeSession]:
        """返回所有已就绪 session 的快照（线程安全读）。"""
        return list(self._active.values())

    # ── 内部异步实现 ──────────────────────────────────────────────────────────

    async def _serve(self) -> None:
        ctrl_srv = await asyncio.start_server(
            self._handle_control, self._host, self._control_port
        )
        data_srv = await asyncio.start_server(
            self._handle_data, self._host, self._data_port
        )
        logger.info(
            "监听 ctrl=%d data=%d", self._control_port, self._data_port
        )
        self._watchdog_task = asyncio.create_task(self._heartbeat_watchdog())
        async with ctrl_srv, data_srv:
            await self._stop_event.wait()  # type: ignore[union-attr]
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        logger.info("SocketServer 已停止")

    async def _signal_stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    # ── 控制通道处理 ──────────────────────────────────────────────────────────

    async def _handle_control(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer_ip, peer_port = writer.get_extra_info("peername")
        logger.debug("控制通道连接: %s:%d", peer_ip, peer_port)
        _protocol_log(
            "control_connected",
            "control channel accepted",
            "wait data channel pairing",
            peer_ip=peer_ip,
            peer_port=peer_port,
        )

        session = NodeSession(peer_ip)
        session.set_control(reader, writer)

        # 检查数据通道是否已先到达
        if peer_ip in self._pending_data:
            dr, dw = self._pending_data.pop(peer_ip)
            evt = self._pending_data_events.pop(peer_ip, None)
            if evt:
                evt.set()
            session.set_data(dr, dw)
            logger.debug("控制通道后配对 data: %s", peer_ip)
            _protocol_log(
                "pair_ready_data_first",
                "control paired with pending data channel",
                "wait HELLO then start data loop",
                peer_ip=peer_ip,
            )
            asyncio.create_task(self._run_data_when_ready(session, dr))
        else:
            _protocol_log(
                "control_pending",
                "control channel accepted",
                "wait data channel pairing",
                peer_ip=peer_ip,
            )
            self._pending_ctrl[peer_ip] = session
            evt = asyncio.Event()
            self._pair_events[peer_ip] = evt
            _protocol_log(
                "pair_wait_start",
                "control pending",
                "data channel should connect within pair timeout",
                peer_ip=peer_ip,
                pair_timeout_sec=_PAIR_TIMEOUT,
            )
            ok = await self._wait_pair(evt)
            self._pair_events.pop(peer_ip, None)

            if not ok:
                self._pending_ctrl.pop(peer_ip, None)
                logger.warning("等待数据通道配对超时: %s", peer_ip)
                _protocol_log(
                    "pair_timeout",
                    "control pending timed out",
                    "client reconnect both channels",
                    peer_ip=peer_ip,
                    pair_timeout_sec=_PAIR_TIMEOUT,
                )
                session.close()
                return

        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=_HELLO_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("等待 HELLO 超时: %s", peer_ip)
            _protocol_log(
                "hello_timeout",
                "paired but no HELLO",
                "client reconnect then send HELLO as first control message",
                peer_ip=peer_ip,
                hello_timeout_sec=_HELLO_TIMEOUT,
            )
            session.close()
            return

        if not raw:
            session.close()
            return

        try:
            msg = decode_control(raw.decode("utf-8"))
        except Exception as exc:
            logger.warning("解析 HELLO 失败 %s: %s", peer_ip, exc)
            session.close()
            return

        if not isinstance(msg, MsgHello):
            logger.warning("期望 HELLO，收到 %s from %s", type(msg).__name__, peer_ip)
            session.close()
            return

        # 初始化 session
        session.node_id = msg.nodeId
        session.node_version = msg.nodeVersion
        session.capabilities = msg.capabilities
        session.current_task_snapshot = msg.currentTask
        session.record_request_id(msg.requestId)
        session.status = "online"
        session.touch()

        # 注册到活跃 sessions（可能覆盖旧断线 session）
        self._active[msg.nodeId] = session
        logger.info("节点上线: %s (%s)", msg.nodeId, peer_ip)
        _protocol_log(
            "hello_received",
            "HELLO parsed and session activated",
            "dispatch callback should send HELLO_ACK",
            node_id=msg.nodeId,
            peer_ip=peer_ip,
        )

        # 回调（例如 DispatchService 处理 sync_actions / HELLO_ACK）
        if self._on_session_ready:
            self._on_session_ready(session)

        # 进入控制消息循环
        await self._control_loop(session, reader)

    async def _control_loop(
        self, session: NodeSession, reader: asyncio.StreamReader
    ) -> None:
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    msg = decode_control(line)
                except Exception as exc:
                    logger.warning("控制消息解析失败 %s: %s", session.node_id, exc)
                    continue

                session.touch()

                # 幂等性检查
                if hasattr(msg, "requestId"):
                    if session.is_duplicate(msg.requestId):
                        logger.debug(
                            "重复 requestId 忽略: %s", msg.requestId
                        )
                        continue
                    session.record_request_id(msg.requestId)

                if self._on_control_message:
                    self._on_control_message(session, msg)
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            pass
        finally:
            await self._handle_session_disconnect(session, reason="control_channel_closed")

    # ── 数据通道处理 ──────────────────────────────────────────────────────────

    async def _handle_data(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer_ip, peer_port = writer.get_extra_info("peername")
        logger.debug("数据通道连接: %s:%d", peer_ip, peer_port)
        _protocol_log(
            "data_connected",
            "data channel accepted",
            "pair with control channel by peer_ip",
            peer_ip=peer_ip,
            peer_port=peer_port,
        )

        if peer_ip in self._pending_ctrl:
            session = self._pending_ctrl.pop(peer_ip)
            session.set_data(reader, writer)
            # 触发配对事件
            if peer_ip in self._pair_events:
                self._pair_events[peer_ip].set()
            logger.debug("数据通道先配对 ctrl: %s", peer_ip)
            # control 先到达时，在 data 侧启动数据读取循环。
            await self._run_data_when_ready(session, reader)
        else:
            # 控制通道还没来，暂存
            self._pending_data[peer_ip] = (reader, writer)
            evt = asyncio.Event()
            self._pending_data_events[peer_ip] = evt
            asyncio.create_task(self._expire_pending_data(peer_ip, evt))
            logger.debug("数据通道暂存等待 ctrl: %s", peer_ip)
            _protocol_log(
                "data_pending",
                "data pending without control",
                "wait control channel to complete pairing",
                peer_ip=peer_ip,
            )
            # 等待控制通道来取走（由控制通道侧 set_data 完成，此处无需再等）
            return

    async def _run_data_when_ready(
        self, session: NodeSession, reader: asyncio.StreamReader
    ) -> None:
        """在 session 有 node_id 后启动 data_loop，覆盖 control/data 先后到达两种路径。"""
        for _ in range(60):  # 最多等 6 秒
            if session.node_id is not None:
                break
            await asyncio.sleep(0.1)

        if session.node_id is None:
            logger.warning("数据通道等待 HELLO 超时: %s", session.peer_ip)
            _protocol_log(
                "data_wait_hello_timeout",
                "data paired but node_id unresolved",
                "control channel should send HELLO",
                peer_ip=session.peer_ip,
            )
            return

        _protocol_log(
            "data_loop_start",
            "data channel ready",
            "receive CHUNK/RESULT frames",
            node_id=session.node_id,
            peer_ip=session.peer_ip,
        )
        await self._data_loop(session, reader)

    async def _data_loop(
        self, session: NodeSession, reader: asyncio.StreamReader
    ) -> None:
        try:
            while True:
                header, payload = await read_data_frame(reader)
                session.touch()
                if self._on_data_frame:
                    self._on_data_frame(session, header, payload)
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            pass
        except Exception as exc:
            logger.exception("数据帧读取异常 %s: %s", session.node_id, exc)
        finally:
            await self._handle_session_disconnect(session, reason="data_channel_closed")

    async def _handle_session_disconnect(self, session: NodeSession, reason: str) -> None:
        """统一收敛断线处理，避免 control/data 双侧重复回调。"""
        if session.status == "offline":
            return

        logger.info("节点断线: %s reason=%s", session.node_id, reason)
        _protocol_log(
            "session_closed",
            f"{reason}",
            "client reconnect with both channels and HELLO",
            node_id=session.node_id,
            peer_ip=session.peer_ip,
        )
        session.close()

        node_id = session.node_id or ""
        active_session = self._active.get(node_id)
        if active_session is session:
            self._active.pop(node_id, None)
            if self._on_session_closed:
                self._on_session_closed(session)

    async def _heartbeat_watchdog(self) -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            now = datetime.now(timezone.utc)
            sessions = list(self._active.values())
            for session in sessions:
                if session.status == "offline":
                    continue
                idle_sec = (now - session.last_seen_at).total_seconds()
                if idle_sec <= _HEARTBEAT_TIMEOUT:
                    continue
                logger.warning(
                    "心跳超时，关闭会话: node=%s idle=%.1fs", session.node_id, idle_sec
                )
                _protocol_log(
                    "heartbeat_timeout",
                    "session idle over heartbeat timeout",
                    "node should reconnect and send HELLO",
                    node_id=session.node_id,
                    peer_ip=session.peer_ip,
                    idle_sec=round(idle_sec, 1),
                    timeout_sec=_HEARTBEAT_TIMEOUT,
                )
                await self._handle_session_disconnect(session, reason="heartbeat_timeout")

    # ── 内部 async 发送 ───────────────────────────────────────────────────────

    async def _send_control_async(self, node_id: str, msg_dict: dict) -> None:
        session = self._active.get(node_id)
        if session is None:
            logger.warning("节点不在线，无法发送控制消息: %s", node_id)
            return
        await session.send_control(msg_dict)

    async def _send_data_async(
        self, node_id: str, header_dict: dict, payload: bytes
    ) -> None:
        session = self._active.get(node_id)
        if session is None:
            logger.warning("节点不在线，无法发送数据帧: %s", node_id)
            return
        await session.send_data_frame(header_dict, payload)

    # ── 工具 ──────────────────────────────────────────────────────────────────

    async def _wait_pair(self, evt: asyncio.Event) -> bool:
        try:
            await asyncio.wait_for(evt.wait(), timeout=_PAIR_TIMEOUT)
            return True
        except asyncio.TimeoutError:
            return False


    async def _expire_pending_data(self, peer_ip: str, evt: asyncio.Event) -> None:
        """清理 data-first 但长期未配对的连接，避免悬挂连接污染重连。"""
        try:
            await asyncio.wait_for(evt.wait(), timeout=_PENDING_DATA_TIMEOUT)
            return
        except asyncio.TimeoutError:
            pass

        pair = self._pending_data.pop(peer_ip, None)
        self._pending_data_events.pop(peer_ip, None)
        if pair is None:
            return

        _reader, writer = pair
        logger.warning("配对超时（控制通道未到达）: %s", peer_ip)
        _protocol_log(
            "pending_data_timeout",
            "data channel timeout without control",
            "client should reconnect both channels",
            peer_ip=peer_ip,
            wait_timeout_sec=_PENDING_DATA_TIMEOUT,
        )
        if writer and not writer.is_closing():
            writer.close()

