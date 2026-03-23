from dataclasses import dataclass
from datetime import datetime
from sqlite3 import Row
from typing import Optional


@dataclass
class Task:
    id: int
    video_path: str
    video_name: str
    speaker_id: str
    status: str
    segment_duration: float
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def from_row(row: Row) -> "Task":
        return Task(
            id=row["id"],
            video_path=row["video_path"],
            video_name=row["video_name"],
            speaker_id=row["speaker_id"],
            status=row["status"],
            segment_duration=row["segment_duration"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


@dataclass
class Segment:
    id: int
    task_id: int
    start_sec: float
    end_sec: float
    heat_score: float
    current_label: Optional[str]

    @staticmethod
    def from_row(row: Row) -> "Segment":
        return Segment(
            id=row["id"],
            task_id=row["task_id"],
            start_sec=row["start_sec"],
            end_sec=row["end_sec"],
            heat_score=row["heat_score"],
            current_label=row["current_label"],
        )


@dataclass
class ThresholdProfile:
    speaker_id: str
    profile_name: str
    min_threshold: float
    max_threshold: float

    @staticmethod
    def from_row(row: Row) -> "ThresholdProfile":
        return ThresholdProfile(
            speaker_id=row["speaker_id"],
            profile_name=row["profile_name"],
            min_threshold=row["min_threshold"],
            max_threshold=row["max_threshold"],
        )


# ── 分发相关模型 ──────────────────────────────────────────────────────────────

@dataclass
class DispatchNode:
    node_id: str
    last_ip: Optional[str]
    capabilities_json: Optional[str]
    status: str  # 'online' | 'busy' | 'offline'
    current_dispatch_id: Optional[int]
    last_seen_at: Optional[datetime]
    registered_at: datetime

    @staticmethod
    def from_row(row: Row) -> "DispatchNode":
        last_seen = None
        if row["last_seen_at"]:
            last_seen = datetime.fromisoformat(row["last_seen_at"])
        return DispatchNode(
            node_id=row["node_id"],
            last_ip=row["last_ip"],
            capabilities_json=row["capabilities_json"],
            status=row["status"],
            current_dispatch_id=row["current_dispatch_id"],
            last_seen_at=last_seen,
            registered_at=datetime.fromisoformat(row["registered_at"]),
        )


@dataclass
class DispatchRecord:
    id: int
    task_id: int
    node_id: str
    dispatch_status: str
    retry_count: int
    error_reason: Optional[str]
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]

    @staticmethod
    def from_row(row: Row) -> "DispatchRecord":
        completed = None
        if row["completed_at"]:
            completed = datetime.fromisoformat(row["completed_at"])
        return DispatchRecord(
            id=row["id"],
            task_id=row["task_id"],
            node_id=row["node_id"],
            dispatch_status=row["dispatch_status"],
            retry_count=row["retry_count"],
            error_reason=row["error_reason"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=completed,
        )


@dataclass
class TransferSession:
    id: int
    dispatch_record_id: int
    transfer_id: str
    direction: str  # 'download' | 'upload'
    file_role: str  # 'video' | 'json' | 'log'
    total_chunks: int
    file_hash: str
    file_size_bytes: int
    status: str
    created_at: datetime
    completed_at: Optional[datetime]

    @staticmethod
    def from_row(row: Row) -> "TransferSession":
        completed = None
        if row["completed_at"]:
            completed = datetime.fromisoformat(row["completed_at"])
        return TransferSession(
            id=row["id"],
            dispatch_record_id=row["dispatch_record_id"],
            transfer_id=row["transfer_id"],
            direction=row["direction"],
            file_role=row["file_role"],
            total_chunks=row["total_chunks"],
            file_hash=row["file_hash"],
            file_size_bytes=row["file_size_bytes"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            completed_at=completed,
        )



@dataclass
class Task:
    id: int
    video_path: str
    video_name: str
    speaker_id: str
    status: str
    segment_duration: float
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def from_row(row: Row) -> "Task":
        return Task(
            id=row["id"],
            video_path=row["video_path"],
            video_name=row["video_name"],
            speaker_id=row["speaker_id"],
            status=row["status"],
            segment_duration=row["segment_duration"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


@dataclass
class Segment:
    id: int
    task_id: int
    start_sec: float
    end_sec: float
    heat_score: float
    current_label: Optional[str]

    @staticmethod
    def from_row(row: Row) -> "Segment":
        return Segment(
            id=row["id"],
            task_id=row["task_id"],
            start_sec=row["start_sec"],
            end_sec=row["end_sec"],
            heat_score=row["heat_score"],
            current_label=row["current_label"],
        )


@dataclass
class ThresholdProfile:
    speaker_id: str
    profile_name: str
    min_threshold: float
    max_threshold: float

    @staticmethod
    def from_row(row: Row) -> "ThresholdProfile":
        return ThresholdProfile(
            speaker_id=row["speaker_id"],
            profile_name=row["profile_name"],
            min_threshold=row["min_threshold"],
            max_threshold=row["max_threshold"],
        )


