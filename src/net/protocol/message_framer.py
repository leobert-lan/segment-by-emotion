"""
帧编解码工具，对应 Kotlin net/protocol/MessageFramer.kt。

控制通道（端口 23010）：换行符分隔 JSON。
数据通道（端口 23011）：[4字节 Big-Endian header长度][header JSON][binary payload]。
"""
from __future__ import annotations

import json
import struct
from typing import Any

from src.net.protocol.control_message import encode_control, decode_control  # noqa: re-export
from src.net.protocol.data_message import decode_data_header


# ── 数据通道帧 ────────────────────────────────────────────────────────────────

async def write_data_frame(
    writer,  # asyncio.StreamWriter
    header_dict: dict[str, Any],
    payload: bytes = b"",
) -> None:
    """向 asyncio.StreamWriter 写入一个数据帧。"""
    header_bytes = json.dumps(header_dict, ensure_ascii=False).encode("utf-8")
    writer.write(struct.pack(">I", len(header_bytes)))
    writer.write(header_bytes)
    if payload:
        writer.write(payload)
    await writer.drain()


async def read_data_frame(reader) -> tuple[Any, bytes]:
    """
    从 asyncio.StreamReader 读取一个数据帧。
    返回 (typed_header_or_dict, payload_bytes)。

    payloadSize 从原始 JSON 中提取（在类型转换前），确保任意消息类型均可读取 payload。
    """
    raw_len = await reader.readexactly(4)
    header_len = struct.unpack(">I", raw_len)[0]
    header_bytes = await reader.readexactly(header_len)

    # 先从原始 JSON 取 payloadSize，再做类型转换（防止类型未知时 payloadSize 丢失）
    raw_dict = json.loads(header_bytes.decode("utf-8"))
    payload_size = int(raw_dict.get("payloadSize", 0))
    payload = await reader.readexactly(payload_size) if payload_size > 0 else b""

    header = decode_data_header(header_bytes)
    return header, payload

