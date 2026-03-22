from dataclasses import dataclass
from pathlib import Path

from src.domain.models import Task
from src.infra.repositories import TaskRepository
from src.services.heat_service import HeatAnalyzer


@dataclass
class BatchImportResult:
    scanned_count: int
    imported_count: int
    heat_generated_count: int
    failed: list[tuple[str, str]]


class TaskIngestService:
    VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".flv"}

    def __init__(self, task_repository: TaskRepository, heat_analyzer: HeatAnalyzer) -> None:
        self.task_repository = task_repository
        self.heat_analyzer = heat_analyzer

    def create_task_and_run_stage1(self, video_path: str, speaker_id: str, segment_duration: float = 5.0) -> Task:
        task = self.create_task_only(video_path=video_path, speaker_id=speaker_id, segment_duration=segment_duration)
        return self.run_stage1_for_task(task.id)

    def create_task_only(self, video_path: str, speaker_id: str, segment_duration: float = 5.0) -> Task:
        task = self.task_repository.create_task(video_path=video_path, speaker_id=speaker_id, segment_duration=segment_duration)
        self.task_repository.update_task_status(task.id, "stage1_pending")
        return self.task_repository.get_task(task.id)

    def run_stage1_for_task(self, task_id: int) -> Task:
        task = self.task_repository.get_task(task_id)
        self.task_repository.update_task_status(task.id, "stage1_running")
        segments = self.heat_analyzer.build_segments(video_path=task.video_path, segment_duration=task.segment_duration)
        self.task_repository.insert_segments(task_id=task.id, segments=segments)
        self.task_repository.update_task_status(task.id, "stage1_done")
        return self.task_repository.get_task(task.id)

    def batch_import_directory(
        self,
        directory_path: str,
        speaker_id: str,
        generate_heat_data: bool = True,
        segment_duration: float = 5.0,
    ) -> BatchImportResult:
        root = Path(directory_path)
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Directory not found: {directory_path}")

        video_files = sorted(
            [path for path in root.iterdir() if path.is_file() and path.suffix.lower() in self.VIDEO_EXTENSIONS],
            key=lambda path: path.name.lower(),
        )
        failed: list[tuple[str, str]] = []
        imported_count = 0
        heat_generated_count = 0

        for video_file in video_files:
            try:
                task = self.create_task_only(str(video_file), speaker_id=speaker_id, segment_duration=segment_duration)
                imported_count += 1
                if generate_heat_data:
                    self.run_stage1_for_task(task.id)
                    heat_generated_count += 1
            except Exception as exc:
                failed.append((str(video_file), str(exc)))

        return BatchImportResult(
            scanned_count=len(video_files),
            imported_count=imported_count,
            heat_generated_count=heat_generated_count,
            failed=failed,
        )

