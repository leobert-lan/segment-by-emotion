import unittest
from types import SimpleNamespace

from src.ui.review_window import ReviewWindow


class ReviewWindowLocalTrackTest(unittest.TestCase):
    def test_focus_index_moves_to_next_segment_on_boundary(self) -> None:
        segments = [
            SimpleNamespace(start_sec=0.0, end_sec=5.0),
            SimpleNamespace(start_sec=5.0, end_sec=10.0),
        ]
        self.assertEqual(ReviewWindow._find_focus_segment_index(segments, 5.0), 1)

    def test_build_local_track_inserts_limited_gaps(self) -> None:
        segments = [
            SimpleNamespace(id=i, start_sec=i * 10.0, end_sec=i * 10.0 + 2.0, heat_score=0.5)
            for i in range(20)
        ]
        items, (start_sec, end_sec) = ReviewWindow._build_local_track(segments, focus_index=10, min_t=0.2, max_t=0.8)

        real_count = sum(1 for item in items if not item.is_gap)
        gap_count = sum(1 for item in items if item.is_gap)

        self.assertEqual(real_count, 9)
        self.assertLessEqual(gap_count, 10)
        self.assertAlmostEqual(start_sec, segments[6].start_sec, places=6)
        self.assertAlmostEqual(end_sec, segments[14].end_sec, places=6)

    def test_find_segment_by_time(self) -> None:
        segments = [
            SimpleNamespace(id=11, start_sec=1.0, end_sec=2.0),
            SimpleNamespace(id=12, start_sec=3.0, end_sec=4.0),
        ]
        found = ReviewWindow._find_segment_by_time(3.2, segments)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.id, 12)


if __name__ == "__main__":
    unittest.main()

