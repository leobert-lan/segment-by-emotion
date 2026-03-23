"""
NodeSession — 单个已连接节点的状态容器。

一个节点持有两条独立 TCP 连接（控制 :23010 + 数据 :23011），
通过来源 IP 进行配对后组成一个完整 Session。
"""
from __future__ import annotations

import asyncio
import collections
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from src.net.protocol.control_message import NodeCapabilities, CurrentTaskSnapshot, encode_control

logger = logging.getLogger(__name__)


class NodeSession:
    """代表一个已连接节点，持有控制 + 数据双通道。"""

    def __init__(self, peer_ip: str) -> None:
        self.peer_ip: str = peer_ip
        self.node_id: Optional[str] = None
        self.node_version: str = ""
        self.capabilities: Optional[NodeCapabilities] = None
        self.current_task_snapshot: Optional[CurrentTaskSnapshot] = None

        # 控制通道（TCP accept 后即设置）
        self.ctrl_reader: Optional[asyncio.StreamReader] = None
        self.ctrl_writer: Optional[asyncio.StreamWriter] = None

        # 数据通道（同 IP 的第二条 TCP accept 后设置）
        self.data_reader: Optional[asyncio.StreamReader] = None
        self.data_writer: Optional[asyncio.StreamWriter] = None

        # 数据通道就绪事件
        self._data_ready: asyncio.Event = asyncio.Event()

        # 节点状态: connecting | online | busy | offline
        self.status: str = "connecting"

        # 当前活跃 dispatch_records.id（None 表示空闲）
        self.active_dispatch_id: Optional[int] = None

        # 幂等缓存：最近 64 条 requestId
        self._seen_request_ids: collections.deque = collections.deque(maxlen=64)

        # 最近心跳时间
        self.last_seen_at: datetime = datetime.now(timezone.utc)

        # 上传时的分片 ACK 事件: chunkIndex → asyncio.Event
        self._ack_events: dict[int, asyncio.Event] = {}

    # ── 通道设置 ──────────────────────────────────────────────────────────────

    def set_control(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self.ctrl_reader = reader
        self.ctrl_writer = writer

    def set_data(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self.data_reader = reader
        self.data_writer = writer
        self._data_ready.set()

    async def wait_for_data_channel(self, timeout: float = 30.0) -> bool:
        """等待数据通道就绪，超时返回 False。"""
        try:
            await asyncio.wait_for(self._data_ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    @property
    def is_paired(self) -> bool:
        return self.ctrl_reader is not None and self.data_reader is not None

    @property
    def is_ready(self) -> bool:
        return self.is_paired and self.node_id is not None

    # ── 幂等性 ────────────────────────────────────────────────────────────────

    def is_duplicate(self, request_id: str) -> bool:
        return request_id in self._seen_request_ids

    def record_request_id(self, request_id: str) -> None:
        self._seen_request_ids.append(request_id)

    # ── 发送 ──────────────────────────────────────────────────────────────────

    async def send_control(self, msg_dict: dict) -> None:
        """向节点发送控制消息（dict 形式）。"""
        if self.ctrl_writer is None or self.ctrl_writer.is_closing():
            raise IOError(f"控制通道未就绪: {self.node_id or self.peer_ip}")
        self.ctrl_writer.write(encode_control(msg_dict))
        await self.ctrl_writer.drain()

    async def send_data_frame(
        self, header_dict: dict[str, Any], payload: bytes = b""
    ) -> None:
        """向节点发送数据帧。"""
        from src.net.protocol.message_framer import write_data_frame
        if self.data_writer is None or self.data_writer.is_closing():
            raise IOError(f"数据通道未就绪: {self.node_id or self.peer_ip}")
        await write_data_frame(self.data_writer, header_dict, payload)

    # ── Upload 分片 ACK 通知 ──────────────────────────────────────────────────

    def notify_chunk_ack(self, chunk_index: int) -> None:
        evt = self._ack_events.get(chunk_index)
        if evt:
            evt.set()

    async def wait_chunk_ack(self, chunk_index: int, timeout: float = 30.0) -> bool:
        evt = asyncio.Event()
        self._ack_events[chunk_index] = evt
        try:
            await asyncio.wait_for(evt.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self._ack_events.pop(chunk_index, None)

    # ── 关闭 ──────────────────────────────────────────────────────────────────

    def close(self) -> None:
        self.status = "offline"
        if self.ctrl_writer and not self.ctrl_writer.is_closing():
            self.ctrl_writer.close()
        if self.data_writer and not self.data_writer.is_closing():
            self.data_writer.close()

    def touch(self) -> None:
        """更新最近心跳时间。"""
        self.last_seen_at = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return (
            f"<NodeSession node_id={self.node_id!r} "
            f"ip={self.peer_ip} status={self.status}>"
        )

