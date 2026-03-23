"""
控制通道消息定义（端口 23010，换行符分隔 JSON）。

与 Kotlin net/protocol/ControlMessage.kt 完全对应：JSON key 使用 camelCase。
classDiscriminator 字段名为 "type"，encodeDefaults=true（所有字段均序列化）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


# ── 嵌套 payload 类型 ─────────────────────────────────────────────────────────

@dataclass
class NodeCapabilities:
    gpu: bool = False
    codec: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"gpu": self.gpu, "codec": self.codec}

    @classmethod
    def from_dict(cls, d: dict) -> "NodeCapabilities":
        return cls(gpu=bool(d.get("gpu", False)), codec=list(d.get("codec") or []))


@dataclass
class CurrentTaskSnapshot:
    taskId: str
    status: str
    progress: float = 0.0

    @classmethod
    def from_dict(cls, d: dict) -> "CurrentTaskSnapshot":
        return cls(
            taskId=str(d["taskId"]),
            status=str(d["status"]),
            progress=float(d.get("progress", 0.0)),
        )


@dataclass
class SyncAction:
    action: str  # "RESUME_UPLOAD" | "QUERY_PROGRESS"
    taskId: str

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "taskId": self.taskId}


@dataclass
class SegmentPayload:
    startMs: int
    endMs: int
    label: str = "interesting"

    def to_dict(self) -> dict[str, Any]:
        return {"startMs": self.startMs, "endMs": self.endMs, "label": self.label}


@dataclass
class VideoMetaPayload:
    videoName: str
    fileSizeBytes: int
    totalChunks: int
    fileHash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "videoName": self.videoName,
            "fileSizeBytes": self.fileSizeBytes,
            "totalChunks": self.totalChunks,
            "fileHash": self.fileHash,
        }


@dataclass
class ProcessingParamsPayload:
    segments: list[SegmentPayload] = field(default_factory=list)
    codecHint: str = "hevc"
    targetBitrateKbps: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "segments": [s.to_dict() for s in self.segments],
            "codecHint": self.codecHint,
            "targetBitrateKbps": self.targetBitrateKbps,
        }


@dataclass
class ResultRequirements:
    includeResultJson: bool = True
    includeLog: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "includeResultJson": self.includeResultJson,
            "includeLog": self.includeLog,
        }


# ── Node → Server 消息 ────────────────────────────────────────────────────────

@dataclass
class MsgHello:
    requestId: str
    nodeId: str
    nodeVersion: str
    capabilities: NodeCapabilities
    currentTask: Optional[CurrentTaskSnapshot] = None

    @classmethod
    def from_dict(cls, d: dict) -> "MsgHello":
        ct = d.get("currentTask")
        return cls(
            requestId=str(d["requestId"]),
            nodeId=str(d["nodeId"]),
            nodeVersion=str(d.get("nodeVersion", "")),
            capabilities=NodeCapabilities.from_dict(d.get("capabilities") or {}),
            currentTask=CurrentTaskSnapshot.from_dict(ct) if ct else None,
        )


@dataclass
class MsgTaskConfirm:
    requestId: str
    taskId: str
    accepted: bool
    reason: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "MsgTaskConfirm":
        return cls(
            requestId=str(d["requestId"]),
            taskId=str(d["taskId"]),
            accepted=bool(d["accepted"]),
            reason=d.get("reason"),
        )


@dataclass
class MsgTaskStatusReport:
    requestId: str
    taskId: str
    status: str
    progress: float = 0.0
    stage: Optional[str] = None
    lastError: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "MsgTaskStatusReport":
        return cls(
            requestId=str(d["requestId"]),
            taskId=str(d["taskId"]),
            status=str(d["status"]),
            progress=float(d.get("progress", 0.0)),
            stage=d.get("stage"),
            lastError=d.get("lastError"),
        )


# ── Server → Node 消息 ────────────────────────────────────────────────────────

@dataclass
class MsgHelloAck:
    requestId: str
    serverTime: str
    syncActions: list[SyncAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "HELLO_ACK",
            "requestId": self.requestId,
            "serverTime": self.serverTime,
            "syncActions": [a.to_dict() for a in self.syncActions],
        }


@dataclass
class MsgTaskAssign:
    requestId: str
    taskId: str
    videoMeta: VideoMetaPayload
    processingParams: ProcessingParamsPayload
    resultRequirements: ResultRequirements = field(default_factory=ResultRequirements)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "TASK_ASSIGN",
            "requestId": self.requestId,
            "taskId": self.taskId,
            "videoMeta": self.videoMeta.to_dict(),
            "processingParams": self.processingParams.to_dict(),
            "resultRequirements": self.resultRequirements.to_dict(),
        }


@dataclass
class MsgTaskStatusQuery:
    requestId: str
    taskId: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "TASK_STATUS_QUERY",
            "requestId": self.requestId,
            "taskId": self.taskId,
        }


# ── 编解码 ────────────────────────────────────────────────────────────────────

ControlMessage = (
    MsgHello
    | MsgTaskConfirm
    | MsgTaskStatusReport
    | MsgHelloAck
    | MsgTaskAssign
    | MsgTaskStatusQuery
)


def encode_control(msg_dict: dict) -> bytes:
    """将消息 dict 编码为 UTF-8 换行终止 JSON。"""
    return (json.dumps(msg_dict, ensure_ascii=False) + "\n").encode("utf-8")


def decode_control(line: str) -> Any:
    """
    解码单行 JSON 控制消息，返回类型化 dataclass 或原始 dict（未知 type）。
    """
    d = json.loads(line.strip())
    t = d.get("type")
    if t == "HELLO":
        return MsgHello.from_dict(d)
    if t == "TASK_CONFIRM":
        return MsgTaskConfirm.from_dict(d)
    if t == "TASK_STATUS_REPORT":
        return MsgTaskStatusReport.from_dict(d)
    return d

