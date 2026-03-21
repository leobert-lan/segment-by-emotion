from src.domain.models import Task
from src.infra.repositories import TaskRepository
from src.services.heat_service import HeatAnalyzer


class TaskIngestService:
    def __init__(self, task_repository: TaskRepository, heat_analyzer: HeatAnalyzer) -> None:
        self.task_repository = task_repository
        self.heat_analyzer = heat_analyzer

    def create_task_and_run_stage1(self, video_path: str, speaker_id: str, segment_duration: float = 5.0) -> Task:
        task = self.task_repository.create_task(video_path=video_path, speaker_id=speaker_id, segment_duration=segment_duration)
        segments = self.heat_analyzer.build_segments(video_path=video_path, segment_duration=segment_duration)
        self.task_repository.insert_segments(task_id=task.id, segments=segments)
        self.task_repository.update_task_status(task.id, "stage1_done")
        return self.task_repository.get_task(task.id)

