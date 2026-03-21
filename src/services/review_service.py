from typing import Optional

from src.domain.models import Segment, Task, ThresholdProfile
from src.infra.repositories import SpeakerProfileRepository, TaskRepository


class ReviewService:
    def __init__(self, task_repository: TaskRepository, profile_repository: SpeakerProfileRepository) -> None:
        self.task_repository = task_repository
        self.profile_repository = profile_repository

    def list_tasks(self) -> list[Task]:
        return self.task_repository.list_tasks()

    def get_task(self, task_id: int) -> Task:
        return self.task_repository.get_task(task_id)

    def list_all_segments(self, task_id: int) -> list[Segment]:
        return self.task_repository.list_segments(task_id=task_id, include_labeled=True)

    def get_task_duration_sec(self, task_id: int) -> float:
        return self.task_repository.get_task_duration_sec(task_id)

    def list_window_segments(self, task_id: int, window_start_sec: float, window_end_sec: float) -> list[Segment]:
        return self.task_repository.list_segments_in_window(
            task_id=task_id,
            window_start_sec=window_start_sec,
            window_end_sec=window_end_sec,
            include_labeled=True,
        )

    def list_candidates(self, task_id: int, min_threshold: float, max_threshold: float) -> list[Segment]:
        return self.task_repository.list_segments_by_threshold(task_id, min_threshold, max_threshold)

    def list_window_candidates(
        self,
        task_id: int,
        min_threshold: float,
        max_threshold: float,
        window_start_sec: float,
        window_end_sec: float,
    ) -> list[Segment]:
        return self.task_repository.list_segments_by_threshold_in_window(
            task_id=task_id,
            min_threshold=min_threshold,
            max_threshold=max_threshold,
            window_start_sec=window_start_sec,
            window_end_sec=window_end_sec,
        )

    def mark_segment(self, task_id: int, segment_id: int, new_label: str) -> None:
        segment = self.task_repository.get_segment(segment_id)
        self.task_repository.update_segment_label(segment_id=segment_id, label=new_label)
        self.task_repository.add_label_event(
            task_id=task_id,
            segment_id=segment_id,
            previous_label=segment.current_label,
            new_label=new_label,
        )
        task = self.task_repository.get_task(task_id)
        if task.status != "review_done":
            self.task_repository.update_task_status(task_id, "review_in_progress")

    def undo_last_mark(self, task_id: int) -> bool:
        event = self.task_repository.last_active_label_event(task_id)
        if event is None:
            return False
        self.task_repository.update_segment_label(segment_id=event["segment_id"], label=event["previous_label"])
        self.task_repository.mark_label_event_undone(event["id"])
        return True

    def complete_review(self, task_id: int) -> None:
        self.task_repository.update_task_status(task_id, "review_done")

    def save_threshold_profile(
        self,
        speaker_id: str,
        min_threshold: float,
        max_threshold: float,
        profile_name: str = "default",
    ) -> None:
        self.profile_repository.upsert_profile(
            speaker_id=speaker_id,
            profile_name=profile_name,
            min_threshold=min_threshold,
            max_threshold=max_threshold,
        )

    def get_threshold_profile(self, speaker_id: str, profile_name: str = "default") -> Optional[ThresholdProfile]:
        return self.profile_repository.get_profile(speaker_id=speaker_id, profile_name=profile_name)

