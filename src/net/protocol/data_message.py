"""
数据通道消息定义（端口 23011，二进制帧）。

与 Kotlin net/protocol/DataMessage.kt 完全对应：JSON key 使用 camelCase。
帧格式：[4字节 Big-Endian header长度][header JSON][binary payload]。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


# ── Node → Server 消息 ────────────────────────────────────────────────────────

@dataclass
class MsgChunkAck:
    """节点确认收到 Chunk（download 方向）。"""
    taskId: str
    transferId: str
    chunkIndex: int
    payloadSize: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "MsgChunkAck":
        return cls(
            taskId=str(d["taskId"]),
            transferId=str(d["transferId"]),
            chunkIndex=int(d["chunkIndex"]),
            payloadSize=int(d.get("payloadSize", 0)),
        )


@dataclass
class MsgTransferResumeRequest:
    """节点重连后请求补发缺失分片。"""
    taskId: str
    transferId: str
    missingIndices: list[int]
    payloadSize: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "MsgTransferResumeRequest":
        return cls(
            taskId=str(d["taskId"]),
            transferId=str(d["transferId"]),
            missingIndices=[int(i) for i in d.get("missingIndices") or []],
            payloadSize=int(d.get("payloadSize", 0)),
        )


@dataclass
class MsgResultChunk:
    """节点上传结果文件分片（upload 方向）。"""
    taskId: str
    transferId: str
    chunkIndex: int
    chunkHash: str
    payloadSize: int
    fileRole: str  # "video" | "json" | "log"

    @classmethod
    def from_dict(cls, d: dict) -> "MsgResultChunk":
        return cls(
            taskId=str(d["taskId"]),
            transferId=str(d["transferId"]),
            chunkIndex=int(d["chunkIndex"]),
            chunkHash=str(d["chunkHash"]),
            payloadSize=int(d["payloadSize"]),
            fileRole=str(d.get("fileRole", "video")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "RESULT_CHUNK",
            "taskId": self.taskId,
            "transferId": self.transferId,
            "chunkIndex": self.chunkIndex,
            "chunkHash": self.chunkHash,
            "payloadSize": self.payloadSize,
            "fileRole": self.fileRole,
        }


@dataclass
class MsgResultTransferComplete:
    """节点所有结果文件上传完毕。"""
    taskId: str
    transferId: str
    totalHash: str
    payloadSize: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "MsgResultTransferComplete":
        return cls(
            taskId=str(d["taskId"]),
            transferId=str(d["transferId"]),
            totalHash=str(d["totalHash"]),
            payloadSize=int(d.get("payloadSize", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "RESULT_TRANSFER_COMPLETE",
            "taskId": self.taskId,
            "transferId": self.transferId,
            "totalHash": self.totalHash,
            "payloadSize": self.payloadSize,
        }


# ── Server → Node 消息 ────────────────────────────────────────────────────────

@dataclass
class MsgChunk:
    """服务端下发视频分片（download 方向）。"""
    taskId: str
    transferId: str
    chunkIndex: int
    chunkHash: str
    payloadSize: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "CHUNK",
            "taskId": self.taskId,
            "transferId": self.transferId,
            "chunkIndex": self.chunkIndex,
            "chunkHash": self.chunkHash,
            "payloadSize": self.payloadSize,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MsgChunk":
        return cls(
            taskId=str(d["taskId"]),
            transferId=str(d["transferId"]),
            chunkIndex=int(d["chunkIndex"]),
            chunkHash=str(d["chunkHash"]),
            payloadSize=int(d["payloadSize"]),
        )


@dataclass
class MsgTransferComplete:
    """服务端通知下载完成，携带文件总 hash。"""
    taskId: str
    transferId: str
    totalHash: str
    payloadSize: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "TRANSFER_COMPLETE",
            "taskId": self.taskId,
            "transferId": self.transferId,
            "totalHash": self.totalHash,
            "payloadSize": self.payloadSize,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MsgTransferComplete":
        return cls(
            taskId=str(d["taskId"]),
            transferId=str(d["transferId"]),
            totalHash=str(d["totalHash"]),
            payloadSize=int(d.get("payloadSize", 0)),
        )


@dataclass
class MsgChunkAckOut:
    """服务端确认收到 ResultChunk（upload 方向）。"""
    taskId: str
    transferId: str
    chunkIndex: int
    payloadSize: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "CHUNK_ACK",
            "taskId": self.taskId,
            "transferId": self.transferId,
            "chunkIndex": self.chunkIndex,
            "payloadSize": self.payloadSize,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MsgChunkAckOut":
        return cls(
            taskId=str(d["taskId"]),
            transferId=str(d["transferId"]),
            chunkIndex=int(d["chunkIndex"]),
            payloadSize=int(d.get("payloadSize", 0)),
        )


# ── 解码 ──────────────────────────────────────────────────────────────────────

DataMessage = (
    MsgChunkAck
    | MsgTransferResumeRequest
    | MsgResultChunk
    | MsgResultTransferComplete
    | MsgChunk
    | MsgTransferComplete
    | MsgChunkAckOut
)


def decode_data_header(header_bytes: bytes) -> Any:
    """
    将数据通道 header 字节解码为类型化 dataclass 或原始 dict（未知 type）。
    同时处理 Node→Server 和 Server→Node 消息类型，方便双向帧测试。
    """
    d = json.loads(header_bytes.decode("utf-8"))
    t = d.get("type")
    # Node → Server
    if t == "CHUNK_ACK":
        return MsgChunkAck.from_dict(d)
    if t == "TRANSFER_RESUME_REQUEST":
        return MsgTransferResumeRequest.from_dict(d)
    if t == "RESULT_CHUNK":
        return MsgResultChunk.from_dict(d)
    if t == "RESULT_TRANSFER_COMPLETE":
        return MsgResultTransferComplete.from_dict(d)
    # Server → Node
    if t == "CHUNK":
        return MsgChunk.from_dict(d)
    if t == "TRANSFER_COMPLETE":
        return MsgTransferComplete.from_dict(d)
    return d
