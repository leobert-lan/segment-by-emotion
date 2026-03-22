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

    def merge_candidate_heat_in_range(self, task_id: int, range_start_sec: float, range_end_sec: float) -> tuple[int, float]:
        start_sec = min(range_start_sec, range_end_sec)
        end_sec = max(range_start_sec, range_end_sec)
        if end_sec <= start_sec:
            return 0, 0.0

        segments = self.task_repository.list_segments_in_window(
            task_id=task_id,
            window_start_sec=start_sec,
            window_end_sec=end_sec,
            include_labeled=False,
        )
        if not segments:
            return 0, 0.0

        max_heat = max(segment.heat_score for segment in segments)
        affected_count = self.task_repository.update_segments_heat_score([segment.id for segment in segments], max_heat)
        return affected_count, max_heat

    def get_duration_stats(self, task_id: int, min_threshold: float, max_threshold: float) -> tuple[float, float]:
        segments = self.list_all_segments(task_id)
        filtered = [
            segment
            for segment in segments
            if segment.current_label is None and min_threshold <= segment.heat_score <= max_threshold
        ]
        interesting = [segment for segment in segments if segment.current_label == "interesting"]
        return self._deduplicated_duration(filtered), self._deduplicated_duration(interesting)

    @staticmethod
    def _deduplicated_duration(segments: list[Segment]) -> float:
        if not segments:
            return 0.0
        intervals = sorted((segment.start_sec, segment.end_sec) for segment in segments)
        merged: list[list[float]] = []
        for start_sec, end_sec in intervals:
            if not merged or start_sec > merged[-1][1]:
                merged.append([start_sec, end_sec])
                continue
            merged[-1][1] = max(merged[-1][1], end_sec)
        return sum(end_sec - start_sec for start_sec, end_sec in merged)

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

    def clear_all_marks(self, task_id: int) -> int:
        cleared_count = self.task_repository.clear_segment_labels_by_task(task_id)
        self.task_repository.mark_task_label_events_undone(task_id)
        if cleared_count > 0:
            task = self.task_repository.get_task(task_id)
            if task.status == "review_done":
                self.task_repository.update_task_status(task_id, "review_in_progress")
        return cleared_count

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

    def smart_mark_segments(
        self,
        task_id: int,
        base_threshold: float,
        max_threshold: float = 1.0,
        high_offset: float = 0.0,
        low_offset: float = 0.05,
    ) -> tuple[int, int, int]:
        if not (0.0 <= base_threshold <= 1.0):
            raise ValueError("base_threshold must be between 0 and 1")
        if not (0.0 <= max_threshold <= 1.0):
            raise ValueError("max_threshold must be between 0 and 1")
        if base_threshold > max_threshold:
            raise ValueError("base_threshold must be less than or equal to max_threshold")
        if high_offset < 0 or low_offset < 0:
            raise ValueError("offsets must be non-negative")

        high_cutoff = min(1.0, base_threshold + high_offset)
        low_cutoff = max(0.0, base_threshold - low_offset)

        segments = self.task_repository.list_segments(task_id=task_id, include_labeled=False)
        interesting_count = 0
        uninteresting_count = 0
        unchanged_count = 0

        for segment in segments:
            if segment.heat_score > max_threshold:
                unchanged_count += 1
                continue
            if segment.heat_score > high_cutoff:
                self.mark_segment(task_id, segment.id, "interesting")
                interesting_count += 1
            elif segment.heat_score < low_cutoff:
                self.mark_segment(task_id, segment.id, "uninteresting")
                uninteresting_count += 1
            else:
                unchanged_count += 1

        return interesting_count, uninteresting_count, unchanged_count

