import atexit
import logging
from pathlib import Path

from src.app.config import AppConfig
from src.infra.db import Database
from src.infra.dispatch_repository import DispatchRepository
from src.infra.repositories import SpeakerProfileRepository, TaskRepository
from src.net.socket.socket_server import SocketServer
from src.services.dispatch_service import DispatchService
from src.services.heat_service import HeatAnalyzer
from src.services.ingest_service import TaskIngestService
from src.services.result_ingest_service import ResultIngestService
from src.services.review_service import ReviewService
from src.services.stage3_stub import Stage3PipelineStub
from src.ui.main_window import MainWindow

logger = logging.getLogger(__name__)


def build_app() -> MainWindow:
    project_root = Path(__file__).resolve().parents[2]
    config = AppConfig.from_project_root(project_root)

    database = Database(config.db_path)
    schema_path = project_root / "src" / "infra" / "schema.sql"
    database.initialize(schema_path)

    task_repository = TaskRepository(database)
    profile_repository = SpeakerProfileRepository(database)
    dispatch_repository = DispatchRepository(database)

    heat_analyzer = HeatAnalyzer()
    ingest_service = TaskIngestService(task_repository, heat_analyzer)
    review_service = ReviewService(task_repository, profile_repository)
    stage3_stub = Stage3PipelineStub()
    result_ingest_service = ResultIngestService()

    # ── Socket 服务端（先构建，回调稍后注入）──────────────────────────────────
    socket_server = SocketServer(
        host=config.server_host,
        control_port=config.control_port,
        data_port=config.data_port,
    )

    dispatch_service = DispatchService(
        socket_server=socket_server,
        dispatch_repo=dispatch_repository,
        task_repo=task_repository,
        result_ingest_service=result_ingest_service,
        results_dir=config.results_dir,
    )

    # 将 dispatch_service 的回调注入 socket_server（避免构造时循环引用）
    socket_server._on_session_ready = dispatch_service.on_session_ready
    socket_server._on_session_closed = dispatch_service.on_session_closed
    socket_server._on_control_message = dispatch_service.on_control_message
    socket_server._on_data_frame = dispatch_service.on_data_frame

    config.results_dir.mkdir(parents=True, exist_ok=True)
    socket_server.start_in_thread()
    logger.info(
        "Socket 服务端已启动: ctrl=:%d data=:%d",
        config.control_port, config.data_port,
    )
    atexit.register(socket_server.stop)

    return MainWindow(
        task_repository=task_repository,
        ingest_service=ingest_service,
        review_service=review_service,
        stage3_stub=stage3_stub,
        dispatch_service=dispatch_service,
        socket_server=socket_server,
    )

