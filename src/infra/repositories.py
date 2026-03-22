from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from src.domain.models import Segment, Task, ThresholdProfile
from src.infra.db import Database


class TaskRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_task(self, video_path: str, speaker_id: str, segment_duration: float) -> Task:
        now = datetime.now(timezone.utc).isoformat()
        video_name = Path(video_path).name
        with self.database.session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tasks (video_path, video_name, speaker_id, status, segment_duration, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (video_path, video_name, speaker_id, "stage1_running", segment_duration, now, now),
            )
            task_id = cursor.lastrowid
        return self.get_task(task_id)

    def get_task(self, task_id: int) -> Task:
        with self.database.session() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"Task not found: {task_id}")
        return Task.from_row(row)

    def list_tasks(self) -> list[Task]:
        with self.database.session() as connection:
            rows = connection.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
        return [Task.from_row(row) for row in rows]

    def update_task_status(self, task_id: int, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.database.session() as connection:
            connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, task_id),
            )

    def delete_task(self, task_id: int) -> int:
        with self.database.session() as connection:
            cursor = connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return int(cursor.rowcount or 0)

    def insert_segments(self, task_id: int, segments: Iterable[tuple[float, float, float]]) -> None:
        with self.database.session() as connection:
            connection.executemany(
                """
                INSERT INTO segments (task_id, start_sec, end_sec, heat_score, current_label)
                VALUES (?, ?, ?, ?, NULL)
                """,
                [(task_id, start_sec, end_sec, score) for (start_sec, end_sec, score) in segments],
            )

    def list_segments(self, task_id: int, include_labeled: bool = True) -> list[Segment]:
        query = "SELECT * FROM segments WHERE task_id = ?"
        params: list[object] = [task_id]
        if not include_labeled:
            query += " AND current_label IS NULL"
        query += " ORDER BY start_sec"
        with self.database.session() as connection:
            rows = connection.execute(query, params).fetchall()
        return [Segment.from_row(row) for row in rows]

    def list_segments_by_threshold(self, task_id: int, min_threshold: float, max_threshold: float) -> list[Segment]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM segments
                WHERE task_id = ?
                  AND current_label IS NULL
                  AND heat_score BETWEEN ? AND ?
                ORDER BY heat_score DESC, start_sec ASC
                """,
                (task_id, min_threshold, max_threshold),
            ).fetchall()
        return [Segment.from_row(row) for row in rows]

    def get_task_duration_sec(self, task_id: int) -> float:
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(end_sec), 0) AS duration_sec FROM segments WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return float(row["duration_sec"]) if row else 0.0

    def list_segments_in_window(
        self,
        task_id: int,
        window_start_sec: float,
        window_end_sec: float,
        include_labeled: bool = True,
    ) -> list[Segment]:
        query = """
            SELECT * FROM segments
            WHERE task_id = ?
              AND end_sec > ?
              AND start_sec < ?
        """
        params: list[object] = [task_id, window_start_sec, window_end_sec]
        if not include_labeled:
            query += " AND current_label IS NULL"
        query += " ORDER BY start_sec"
        with self.database.session() as connection:
            rows = connection.execute(query, params).fetchall()
        return [Segment.from_row(row) for row in rows]

    def list_segments_by_threshold_in_window(
        self,
        task_id: int,
        min_threshold: float,
        max_threshold: float,
        window_start_sec: float,
        window_end_sec: float,
    ) -> list[Segment]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM segments
                WHERE task_id = ?
                  AND current_label IS NULL
                  AND heat_score BETWEEN ? AND ?
                  AND end_sec > ?
                  AND start_sec < ?
                ORDER BY heat_score DESC, start_sec ASC
                """,
                (task_id, min_threshold, max_threshold, window_start_sec, window_end_sec),
            ).fetchall()
        return [Segment.from_row(row) for row in rows]

    def update_segment_label(self, segment_id: int, label: Optional[str]) -> None:
        with self.database.session() as connection:
            connection.execute(
                "UPDATE segments SET current_label = ? WHERE id = ?",
                (label, segment_id),
            )

    def clear_segment_labels_by_task(self, task_id: int) -> int:
        with self.database.session() as connection:
            cursor = connection.execute(
                "UPDATE segments SET current_label = NULL WHERE task_id = ? AND current_label IS NOT NULL",
                (task_id,),
            )
        return int(cursor.rowcount or 0)

    def update_segments_heat_score(self, segment_ids: list[int], new_heat_score: float) -> int:
        if not segment_ids:
            return 0
        placeholders = ",".join("?" for _ in segment_ids)
        params = [new_heat_score, *segment_ids]
        with self.database.session() as connection:
            cursor = connection.execute(
                f"UPDATE segments SET heat_score = ? WHERE id IN ({placeholders})",
                params,
            )
        return int(cursor.rowcount or 0)

    def get_segment(self, segment_id: int) -> Segment:
        with self.database.session() as connection:
            row = connection.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()
        if row is None:
            raise ValueError(f"Segment not found: {segment_id}")
        return Segment.from_row(row)

    def add_label_event(self, task_id: int, segment_id: int, previous_label: Optional[str], new_label: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.database.session() as connection:
            connection.execute(
                """
                INSERT INTO label_events (task_id, segment_id, previous_label, new_label, undone, created_at)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (task_id, segment_id, previous_label, new_label, now),
            )

    def last_active_label_event(self, task_id: int):
        with self.database.session() as connection:
            return connection.execute(
                """
                SELECT * FROM label_events
                WHERE task_id = ? AND undone = 0
                ORDER BY id DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()

    def mark_label_event_undone(self, event_id: int) -> None:
        with self.database.session() as connection:
            connection.execute("UPDATE label_events SET undone = 1 WHERE id = ?", (event_id,))

    def mark_task_label_events_undone(self, task_id: int) -> int:
        with self.database.session() as connection:
            cursor = connection.execute(
                "UPDATE label_events SET undone = 1 WHERE task_id = ? AND undone = 0",
                (task_id,),
            )
        return int(cursor.rowcount or 0)

    def count_segments(self, task_id: int) -> int:
        with self.database.session() as connection:
            row = connection.execute("SELECT COUNT(*) AS cnt FROM segments WHERE task_id = ?", (task_id,)).fetchone()
        return int(row["cnt"])


class SpeakerProfileRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert_profile(self, speaker_id: str, profile_name: str, min_threshold: float, max_threshold: float) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.database.session() as connection:
            connection.execute(
                """
                INSERT INTO threshold_profiles (speaker_id, profile_name, min_threshold, max_threshold, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(speaker_id, profile_name)
                DO UPDATE SET
                    min_threshold = excluded.min_threshold,
                    max_threshold = excluded.max_threshold,
                    updated_at = excluded.updated_at
                """,
                (speaker_id, profile_name, min_threshold, max_threshold, now),
            )

    def get_profile(self, speaker_id: str, profile_name: str = "default") -> Optional[ThresholdProfile]:
        with self.database.session() as connection:
            row = connection.execute(
                """
                SELECT * FROM threshold_profiles
                WHERE speaker_id = ? AND profile_name = ?
                """,
                (speaker_id, profile_name),
            ).fetchone()
        return ThresholdProfile.from_row(row) if row else None

