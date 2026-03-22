import csv
import json
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

    def test_clear_all_marks_resets_labels_and_undo_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "test.db"
            schema_path = Path(__file__).resolve().parents[1] / "src" / "infra" / "schema.sql"

            database = Database(db_path)
            database.initialize(schema_path)
            task_repo = TaskRepository(database)
            profile_repo = SpeakerProfileRepository(database)
            review = ReviewService(task_repo, profile_repo)

            task = task_repo.create_task("demo.mp4", "speaker_a", segment_duration=2.0)
            task_repo.insert_segments(task.id, [(0.0, 2.0, 0.7), (2.0, 4.0, 0.4), (4.0, 6.0, 0.6)])
            segments = task_repo.list_segments(task.id, include_labeled=True)
            review.mark_segment(task.id, segments[0].id, "interesting")
            review.mark_segment(task.id, segments[1].id, "uninteresting")

            cleared_count = review.clear_all_marks(task.id)

            self.assertEqual(cleared_count, 2)
            refreshed = task_repo.list_segments(task.id, include_labeled=True)
            self.assertTrue(all(segment.current_label is None for segment in refreshed))
            self.assertFalse(review.undo_last_mark(task.id))

    def test_smart_mark_segments_uses_threshold_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "test.db"
            schema_path = Path(__file__).resolve().parents[1] / "src" / "infra" / "schema.sql"

            database = Database(db_path)
            database.initialize(schema_path)
            task_repo = TaskRepository(database)
            profile_repo = SpeakerProfileRepository(database)
            review = ReviewService(task_repo, profile_repo)

            task = task_repo.create_task("demo.mp4", "speaker_a", segment_duration=2.0)
            task_repo.insert_segments(
                task.id,
                [
                    (0.0, 2.0, 0.71),  # > 0.60 + 0.10 => interesting
                    (2.0, 4.0, 0.49),  # < 0.60 - 0.05 => uninteresting
                    (4.0, 6.0, 0.56),  # in between => unchanged
                    (6.0, 8.0, 0.70),  # boundary (== high cutoff) => unchanged
                    (8.0, 10.0, 0.55),  # boundary (== low cutoff) => unchanged
                ],
            )

            interesting_count, uninteresting_count, unchanged_count = review.smart_mark_segments(
                task.id,
                base_threshold=0.60,
                high_offset=0.10,
                low_offset=0.05,
            )

            self.assertEqual(interesting_count, 1)
            self.assertEqual(uninteresting_count, 1)
            self.assertEqual(unchanged_count, 3)

            segments = task_repo.list_segments(task.id, include_labeled=True)
            labels = {segment.heat_score: segment.current_label for segment in segments}
            self.assertEqual(labels[0.71], "interesting")
            self.assertEqual(labels[0.49], "uninteresting")
            self.assertIsNone(labels[0.56])
            self.assertIsNone(labels[0.70])
            self.assertIsNone(labels[0.55])

    def test_smart_mark_segments_uses_new_default_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "test.db"
            schema_path = Path(__file__).resolve().parents[1] / "src" / "infra" / "schema.sql"

            database = Database(db_path)
            database.initialize(schema_path)
            task_repo = TaskRepository(database)
            profile_repo = SpeakerProfileRepository(database)
            review = ReviewService(task_repo, profile_repo)

            task = task_repo.create_task("demo.mp4", "speaker_a", segment_duration=2.0)
            task_repo.insert_segments(
                task.id,
                [
                    (0.0, 2.0, 0.41),  # > 0.40 + 0.00 => interesting
                    (2.0, 4.0, 0.34),  # < 0.40 - 0.05 => uninteresting
                    (4.0, 6.0, 0.36),  # unchanged
                ],
            )

            interesting_count, uninteresting_count, unchanged_count = review.smart_mark_segments(
                task.id,
                base_threshold=0.40,
            )

            self.assertEqual((interesting_count, uninteresting_count, unchanged_count), (1, 1, 1))

    def test_smart_mark_segments_skips_scores_above_max_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "test.db"
            schema_path = Path(__file__).resolve().parents[1] / "src" / "infra" / "schema.sql"

            database = Database(db_path)
            database.initialize(schema_path)
            task_repo = TaskRepository(database)
            profile_repo = SpeakerProfileRepository(database)
            review = ReviewService(task_repo, profile_repo)

            task = task_repo.create_task("demo.mp4", "speaker_a", segment_duration=2.0)
            task_repo.insert_segments(
                task.id,
                [
                    (0.0, 2.0, 0.76),  # above max threshold, should remain unchanged
                    (2.0, 4.0, 0.66),  # > base + high_offset => interesting
                    (4.0, 6.0, 0.50),  # < base - low_offset => uninteresting
                ],
            )

            interesting_count, uninteresting_count, unchanged_count = review.smart_mark_segments(
                task.id,
                base_threshold=0.60,
                max_threshold=0.70,
                high_offset=0.00,
                low_offset=0.05,
            )

            self.assertEqual((interesting_count, uninteresting_count, unchanged_count), (1, 1, 1))

            segments = task_repo.list_segments(task.id, include_labeled=True)
            labels = {segment.heat_score: segment.current_label for segment in segments}
            self.assertIsNone(labels[0.76])
            self.assertEqual(labels[0.66], "interesting")
            self.assertEqual(labels[0.50], "uninteresting")

    def test_export_heat_data_writes_json_and_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "test.db"
            export_dir = root / "exports"
            schema_path = Path(__file__).resolve().parents[1] / "src" / "infra" / "schema.sql"

            database = Database(db_path)
            database.initialize(schema_path)
            task_repo = TaskRepository(database)
            profile_repo = SpeakerProfileRepository(database)
            review = ReviewService(task_repo, profile_repo)

            task = task_repo.create_task("demo.mp4", "speaker_a", segment_duration=2.0)
            task_repo.insert_segments(task.id, [(0.0, 2.0, 0.61), (2.0, 4.0, 0.34)])
            first_segment = task_repo.list_segments(task.id, include_labeled=True)[0]
            review.mark_segment(task.id, first_segment.id, "interesting")

            json_path, csv_path, segment_count = review.export_heat_data(task.id, export_dir)

            self.assertEqual(segment_count, 2)
            self.assertTrue(json_path.exists())
            self.assertTrue(csv_path.exists())

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["task"]["id"], task.id)
            self.assertEqual(len(payload["segments"]), 2)
            self.assertGreaterEqual(len(payload["label_events"]), 1)

            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["task_id"], str(task.id))
            self.assertIn("heat_score", rows[0])


if __name__ == "__main__":
    unittest.main()

