from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    data_dir: Path
    db_path: Path


    @staticmethod
    def from_project_root(project_root: Path) -> "AppConfig":
        data_dir = project_root / "data"
        db_path = data_dir / "segment_by_motion.db"
        return AppConfig(project_root=project_root, data_dir=data_dir, db_path=db_path)

