from pathlib import Path

from src.app.config import AppConfig
from src.infra.db import Database
from src.infra.repositories import SpeakerProfileRepository, TaskRepository
from src.services.heat_service import HeatAnalyzer
from src.services.ingest_service import TaskIngestService
from src.services.review_service import ReviewService
from src.services.stage3_stub import Stage3PipelineStub
from src.ui.main_window import MainWindow


def build_app() -> MainWindow:
    project_root = Path(__file__).resolve().parents[2]
    config = AppConfig.from_project_root(project_root)

    database = Database(config.db_path)
    schema_path = project_root / "src" / "infra" / "schema.sql"
    database.initialize(schema_path)

    task_repository = TaskRepository(database)
    profile_repository = SpeakerProfileRepository(database)

    heat_analyzer = HeatAnalyzer()
    ingest_service = TaskIngestService(task_repository, heat_analyzer)
    review_service = ReviewService(task_repository, profile_repository)
    stage3_stub = Stage3PipelineStub()

    return MainWindow(
        task_repository=task_repository,
        ingest_service=ingest_service,
        review_service=review_service,
        stage3_stub=stage3_stub,
    )

