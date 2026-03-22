import tempfile
import unittest
from pathlib import Path

from src.infra.db import Database
from src.infra.repositories import SpeakerProfileRepository, TaskRepository
from src.services.review_service import ReviewService


class ReviewServiceStatsTest(unittest.TestCase):
    def test_duration_stats_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "test.db"
            schema_path = Path(__file__).resolve().parents[1] / "src" / "infra" / "schema.sql"

            database = Database(db_path)
            database.initialize(schema_path)
            task_repo = TaskRepository(database)
            profile_repo = SpeakerProfileRepository(database)
            review = ReviewService(task_repo, profile_repo)

            task = task_repo.create_task("demo.mp4", "speaker_a", segment_duration=5.0)
            task_repo.insert_segments(
                task.id,
                [
                    (0.0, 5.0, 0.3),
                    (4.0, 9.0, 0.5),
                    (12.0, 14.0, 0.8),
                    (13.0, 15.0, 0.9),
                ],
            )
            all_segments = task_repo.list_segments(task.id, include_labeled=True)
            task_repo.update_segment_label(all_segments[0].id, "interesting")
            task_repo.update_segment_label(all_segments[1].id, "interesting")

            filtered_sec, interesting_sec = review.get_duration_stats(task.id, min_threshold=0.4, max_threshold=1.0)

            self.assertAlmostEqual(filtered_sec, 3.0, places=6)
            self.assertAlmostEqual(interesting_sec, 9.0, places=6)

    def test_merge_candidate_heat_in_range_updates_unlabeled_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "test.db"
            schema_path = Path(__file__).resolve().parents[1] / "src" / "infra" / "schema.sql"

            database = Database(db_path)
            database.initialize(schema_path)
            task_repo = TaskRepository(database)
            profile_repo = SpeakerProfileRepository(database)
            review = ReviewService(task_repo, profile_repo)

            task = task_repo.create_task("demo.mp4", "speaker_a", segment_duration=5.0)
            task_repo.insert_segments(
                task.id,
                [
                    (0.0, 2.0, 0.2),
                    (2.0, 4.0, 0.7),
                    (4.0, 6.0, 0.5),
                ],
            )
            segments = task_repo.list_segments(task.id, include_labeled=True)
            task_repo.update_segment_label(segments[0].id, "interesting")

            count, max_heat = review.merge_candidate_heat_in_range(task.id, 0.0, 6.0)
            refreshed = task_repo.list_segments(task.id, include_labeled=True)

            self.assertEqual(count, 2)
            self.assertAlmostEqual(max_heat, 0.7, places=6)
            self.assertAlmostEqual(refreshed[0].heat_score, 0.2, places=6)
            self.assertAlmostEqual(refreshed[1].heat_score, 0.7, places=6)
            self.assertAlmostEqual(refreshed[2].heat_score, 0.7, places=6)

    def test_delete_task_cascades_segments_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "test.db"
            schema_path = Path(__file__).resolve().parents[1] / "src" / "infra" / "schema.sql"

            database = Database(db_path)
            database.initialize(schema_path)
            task_repo = TaskRepository(database)

            task = task_repo.create_task("demo.mp4", "speaker_a", segment_duration=5.0)
            task_repo.insert_segments(task.id, [(0.0, 2.0, 0.6)])
            segment = task_repo.list_segments(task.id, include_labeled=True)[0]
            task_repo.add_label_event(task.id, segment.id, None, "interesting")

            deleted = task_repo.delete_task(task.id)

            self.assertEqual(deleted, 1)
            self.assertEqual(task_repo.list_segments(task.id, include_labeled=True), [])
            with database.session() as connection:
                event_count = connection.execute(
                    "SELECT COUNT(*) AS cnt FROM label_events WHERE task_id = ?",
                    (task.id,),
                ).fetchone()["cnt"]
                task_count = connection.execute(
                    "SELECT COUNT(*) AS cnt FROM tasks WHERE id = ?",
                    (task.id,),
                ).fetchone()["cnt"]
            self.assertEqual(event_count, 0)
            self.assertEqual(task_count, 0)


if __name__ == "__main__":
    unittest.main()

