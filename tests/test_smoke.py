import tempfile
import unittest
from pathlib import Path

from src.infra.db import Database
from src.infra.repositories import SpeakerProfileRepository, TaskRepository
from src.services.heat_service import HeatAnalyzer
from src.services.ingest_service import TaskIngestService
from src.services.review_service import ReviewService


class SmokeFlowTest(unittest.TestCase):
    def test_task_review_profile_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "test.db"
            schema_path = Path(__file__).resolve().parents[1] / "src" / "infra" / "schema.sql"

            dummy_video = root / "sample.mp4"
            dummy_video.write_bytes(b"0" * 2048)

            database = Database(db_path)
            database.initialize(schema_path)

            task_repo = TaskRepository(database)
            profile_repo = SpeakerProfileRepository(database)
            analyzer = HeatAnalyzer()
            analyzer._try_load_audio = lambda _path: (None, None)  # type: ignore[method-assign]
            ingest = TaskIngestService(task_repo, analyzer)
            review = ReviewService(task_repo, profile_repo)

            task = ingest.create_task_and_run_stage1(str(dummy_video), "speaker_alpha", segment_duration=5.0)
            self.assertEqual(task.status, "stage1_done")

            duration = review.get_task_duration_sec(task.id)
            self.assertGreater(duration, 0.0)

            window_segments = review.list_window_segments(task.id, 0.0, 30.0)
            self.assertGreater(len(window_segments), 0)

            candidates = review.list_candidates(task.id, 0.2, 1.0)
            self.assertGreater(len(candidates), 0)

            window_candidates = review.list_window_candidates(task.id, 0.2, 1.0, 0.0, 30.0)
            self.assertGreater(len(window_candidates), 0)

            first = candidates[0]
            review.mark_segment(task.id, first.id, "interesting")
            remaining = review.list_candidates(task.id, 0.2, 1.0)
            self.assertNotIn(first.id, [segment.id for segment in remaining])

            self.assertTrue(review.undo_last_mark(task.id))
            reloaded = review.list_candidates(task.id, 0.2, 1.0)
            self.assertIn(first.id, [segment.id for segment in reloaded])

            review.save_threshold_profile("speaker_alpha", 0.35, 0.92)
            profile = review.get_threshold_profile("speaker_alpha")
            self.assertIsNotNone(profile)
            assert profile is not None
            self.assertAlmostEqual(profile.min_threshold, 0.35, places=6)
            self.assertAlmostEqual(profile.max_threshold, 0.92, places=6)


if __name__ == "__main__":
    unittest.main()

