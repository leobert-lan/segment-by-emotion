import tempfile
import unittest
from pathlib import Path

from src.infra.db import Database
from src.infra.repositories import TaskRepository
from src.services.heat_service import HeatAnalyzer
from src.services.ingest_service import TaskIngestService


class TaskIngestServiceBatchTest(unittest.TestCase):
    def test_batch_import_directory_without_heat_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "videos"
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / "a.mp4").write_bytes(b"0" * 64)
            (data_dir / "b.mkv").write_bytes(b"1" * 64)
            (data_dir / "ignore.txt").write_text("not video", encoding="utf-8")

            database = Database(root / "test.db")
            schema_path = Path(__file__).resolve().parents[1] / "src" / "infra" / "schema.sql"
            database.initialize(schema_path)

            task_repo = TaskRepository(database)
            analyzer = HeatAnalyzer()
            analyzer._try_load_audio = lambda _path: (None, None)  # type: ignore[method-assign]
            ingest = TaskIngestService(task_repo, analyzer)

            result = ingest.batch_import_directory(str(data_dir), "speaker_a", generate_heat_data=False)

            self.assertEqual(result.scanned_count, 2)
            self.assertEqual(result.imported_count, 2)
            self.assertEqual(result.heat_generated_count, 0)
            self.assertEqual(len(result.failed), 0)

            tasks = task_repo.list_tasks()
            self.assertEqual(len(tasks), 2)
            self.assertTrue(all(task.status == "stage1_pending" for task in tasks))
            self.assertTrue(all(task_repo.count_segments(task.id) == 0 for task in tasks))

    def test_batch_import_directory_with_heat_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "videos"
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / "a.mp4").write_bytes(b"0" * 64)
            (data_dir / "b.avi").write_bytes(b"1" * 64)

            database = Database(root / "test.db")
            schema_path = Path(__file__).resolve().parents[1] / "src" / "infra" / "schema.sql"
            database.initialize(schema_path)

            task_repo = TaskRepository(database)
            analyzer = HeatAnalyzer()
            analyzer._try_load_audio = lambda _path: (None, None)  # type: ignore[method-assign]
            ingest = TaskIngestService(task_repo, analyzer)

            result = ingest.batch_import_directory(str(data_dir), "speaker_a", generate_heat_data=True)

            self.assertEqual((result.scanned_count, result.imported_count, result.heat_generated_count), (2, 2, 2))
            self.assertEqual(len(result.failed), 0)

            tasks = task_repo.list_tasks()
            self.assertEqual(len(tasks), 2)
            self.assertTrue(all(task.status == "stage1_done" for task in tasks))
            self.assertTrue(all(task_repo.count_segments(task.id) > 0 for task in tasks))


if __name__ == "__main__":
    unittest.main()

