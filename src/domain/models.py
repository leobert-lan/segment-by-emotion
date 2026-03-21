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

