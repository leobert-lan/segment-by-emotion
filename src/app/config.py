from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    data_dir: Path
    db_path: Path
    # Socket 服务端
    server_host: str
    control_port: int   # 控制通道（:23010）
    data_port: int      # 数据通道（:23011）
    results_dir: Path   # 节点结果落盘根目录

    @staticmethod
    def from_project_root(project_root: Path) -> "AppConfig":
        data_dir = project_root / "data"
        return AppConfig(
            project_root=project_root,
            data_dir=data_dir,
            db_path=data_dir / "segment_by_motion.db",
            server_host="0.0.0.0",
            control_port=23010,
            data_port=23011,
            results_dir=data_dir / "node_results",
        )

