import unittest

from src.ui.main_window import MainWindow


class TestMainWindowStatusFilter(unittest.TestCase):
    def test_resolve_display_status_running_from_dispatch(self) -> None:
        for dispatch_status in ("confirmed", "transferring", "running", "uploading"):
            self.assertEqual(
                MainWindow._resolve_display_status("review_done", dispatch_status),
                "running",
            )

    def test_resolve_display_status_completed_from_done(self) -> None:
        self.assertEqual(
            MainWindow._resolve_display_status("review_done", "done"),
            "completed",
        )

    def test_resolve_display_status_fallback_to_task_status(self) -> None:
        self.assertEqual(
            MainWindow._resolve_display_status("review_done", None),
            "review_done",
        )

    def test_status_filter_match(self) -> None:
        self.assertTrue(MainWindow._status_filter_match("running", "全部"))
        self.assertTrue(MainWindow._status_filter_match("running", "running"))
        self.assertFalse(MainWindow._status_filter_match("completed", "running"))

    def test_ordered_statuses_prefers_running_completed(self) -> None:
        statuses = {"review_done", "completed", "running", "stage1_done"}
        self.assertEqual(
            MainWindow._ordered_statuses(statuses),
            ["running", "completed", "review_done", "stage1_done"],
        )


if __name__ == "__main__":
    unittest.main()

