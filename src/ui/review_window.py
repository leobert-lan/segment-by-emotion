import tkinter as tk
import os
import struct
from queue import Empty, Queue
from threading import Thread
from dataclasses import dataclass
from tkinter import messagebox, ttk

from src.services.review_service import ReviewService


@dataclass
class TrackItem:
    start_sec: float
    end_sec: float
    is_gap: bool
    in_threshold: bool = False


class ReviewWindow(ttk.Frame):
    def __init__(self, master: tk.Widget, review_service: ReviewService, on_task_refresh, on_back_to_tasks=None) -> None:
        super().__init__(master)
        self.review_service = review_service
        self.on_task_refresh = on_task_refresh
        self.on_back_to_tasks = on_back_to_tasks
        self.current_task_id: int | None = None

        self.task_info_var = tk.StringVar(value="当前任务: 未选择")
        self.threshold_min_var = tk.StringVar(value="0.40")
        self.threshold_max_var = tk.StringVar(value="1.00")
        self.smart_high_offset_var = tk.StringVar(value="0.00")
        self.smart_low_offset_var = tk.StringVar(value="0.05")
        self.window_duration_var = tk.StringVar(value="20000")
        self.window_start_var = tk.StringVar(value="0")
        self.timeline_info_var = tk.StringVar(value="时间窗 0s - 0s / 总时长 0s")
        self.duration_summary_var = tk.StringVar(value="筛选时长 00:00 (0.0%) | 有趣时长 00:00 (0.0%)")
        self.seek_info_var = tk.StringVar(value="当前定位: 00:00")
        self.edit_state_var = tk.StringVar(value="编辑: 未启用")

        self.segments_tree = None
        self.heat_canvas = None
        self.local_progress_canvas = None
        self.total_duration_sec = 0.0
        self.current_seek_sec = 0.0

        self._vlc = None
        self._vlc_instance = None
        self._player = None
        self._player_ready = False
        self._player_available = False
        self._player_init_attempted = False
        self._player_error_detail = ""
        self._selected_vlc_dir: str | None = None
        self._player_time_after_id: str | None = None
        self._heatline_redraw_after_id: str | None = None
        self._local_progress_redraw_after_id: str | None = None

        self._candidate_segments = []
        self._candidate_index = -1
        self._candidate_tick_after_id: str | None = None

        self.video_panel = None
        self.video_status_var = tk.StringVar(value="播放器状态: 未初始化")
        self.candidate_loop_var = tk.BooleanVar(value=False)
        self.playback_rate_var = tk.StringVar(value="1.0")
        self.volume_var = tk.IntVar(value=70)

        self._window_candidates = []
        self._all_segments_cache = []
        self._local_track_items: list[TrackItem] = []
        self._local_track_range = (0.0, 0.0)
        self._edit_boundary: str | None = None
        self._merge_start_sec: float | None = None
        self._merge_end_sec: float | None = None
        self._edit_step_sec = 0.2
        self._dragging_boundary: str | None = None
        self._drag_min_gap_sec = 0.1
        self._drag_hit_px = 8
        self._tree_sort_column = "start"
        self._tree_sort_desc = False

        self.smart_mark_button: ttk.Button | None = None
        self._smart_mark_thread: Thread | None = None
        self._smart_mark_result_queue: Queue = Queue()
        self._smart_mark_poll_after_id: str | None = None
        self._smart_mark_loading_dialog: tk.Toplevel | None = None
        self._smart_mark_loading_progress: ttk.Progressbar | None = None

        self._build_layout()
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _build_layout(self) -> None:
        controls = ttk.LabelFrame(self, text="筛选与操作")
        controls.pack(fill="x", padx=10, pady=8)

        task_row = ttk.Frame(controls)
        task_row.pack(fill="x", padx=8, pady=(6, 4))
        ttk.Label(task_row, textvariable=self.task_info_var).pack(side="left")
        if self.on_back_to_tasks is not None:
            ttk.Button(task_row, text="返回任务管理", command=self.on_back_to_tasks_clicked).pack(side="right")

        window_row = ttk.Frame(controls)
        window_row.pack(fill="x", padx=8, pady=4)
        ttk.Label(window_row, text="窗口时长(秒)").pack(side="left")
        ttk.Entry(window_row, textvariable=self.window_duration_var, width=10).pack(side="left", padx=(4, 12))
        ttk.Label(window_row, text="窗口起点(秒)").pack(side="left")
        ttk.Entry(window_row, textvariable=self.window_start_var, width=10).pack(side="left", padx=4)
        ttk.Button(window_row, text="加载窗口", command=self.reload_window).pack(side="left", padx=(8, 4))
        ttk.Button(window_row, text="上一段", command=self.prev_window).pack(side="left", padx=4)
        ttk.Button(window_row, text="下一段", command=self.next_window).pack(side="left", padx=4)

        threshold_row = ttk.Frame(controls)
        threshold_row.pack(fill="x", padx=8, pady=4)
        ttk.Label(threshold_row, text="阈值最小").pack(side="left")
        ttk.Entry(threshold_row, textvariable=self.threshold_min_var, width=8).pack(side="left", padx=(4, 12))
        ttk.Label(threshold_row, text="阈值最大").pack(side="left")
        ttk.Entry(threshold_row, textvariable=self.threshold_max_var, width=8).pack(side="left", padx=4)
        ttk.Label(threshold_row, text="智能有趣偏移").pack(side="left", padx=(12, 2))
        ttk.Entry(threshold_row, textvariable=self.smart_high_offset_var, width=6).pack(side="left", padx=(0, 8))
        ttk.Label(threshold_row, text="智能无趣偏移").pack(side="left", padx=(0, 2))
        ttk.Entry(threshold_row, textvariable=self.smart_low_offset_var, width=6).pack(side="left", padx=(0, 8))
        ttk.Button(threshold_row, text="应用筛选", command=self.refresh_candidates).pack(side="left", padx=(8, 4))
        ttk.Button(threshold_row, text="加载说话人档案", command=self.load_profile).pack(side="left", padx=4)
        ttk.Button(threshold_row, text="保存说话人档案", command=self.save_profile).pack(side="left", padx=4)

        action_row = ttk.Frame(controls)
        action_row.pack(fill="x", padx=8, pady=(4, 8))
        self.smart_mark_button = ttk.Button(action_row, text="智能标记", command=self.smart_mark)
        self.smart_mark_button.pack(side="left", padx=4)
        ttk.Button(action_row, text="清除所有标记", command=self.clear_all_marks).pack(side="left", padx=4)
        ttk.Button(action_row, text="标记有趣 (I)", command=lambda: self.mark_selected("interesting")).pack(side="left", padx=4)
        ttk.Button(action_row, text="标记无趣 (U)", command=lambda: self.mark_selected("uninteresting")).pack(side="left", padx=4)
        ttk.Button(action_row, text="撤销上次标记", command=self.undo_last).pack(side="left", padx=4)
        ttk.Button(action_row, text="完成Review", command=self.complete_review).pack(side="left", padx=4)
        ttk.Separator(action_row, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(action_row, text="加载当前视频", command=self._load_media_for_current_task).pack(side="left", padx=4)
        ttk.Button(action_row, text="从当前定位播放", command=self.play_from_current_seek).pack(side="left", padx=4)
        ttk.Button(action_row, text="播放所选片段", command=self.play_selected_segment).pack(side="left", padx=4)
        ttk.Label(action_row, textvariable=self.seek_info_var).pack(side="right")

        video_wrap = ttk.LabelFrame(self, text="视频预览")
        video_wrap.pack(fill="x", padx=10, pady=(0, 8))
        self.video_panel = tk.Frame(video_wrap, bg="black", height=280)
        self.video_panel.pack(fill="x", padx=8, pady=8)

        player_controls = ttk.Frame(video_wrap)
        player_controls.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(player_controls, text="播放", command=self.play_current).pack(side="left", padx=4)
        ttk.Button(player_controls, text="暂停", command=self.pause_current).pack(side="left", padx=4)
        ttk.Button(player_controls, text="停止", command=self.stop_current).pack(side="left", padx=4)
        ttk.Checkbutton(
            player_controls,
            text="仅播放候选片段",
            variable=self.candidate_loop_var,
            command=self.on_toggle_candidate_loop,
        ).pack(side="left", padx=4)

        ttk.Label(player_controls, text="倍速").pack(side="left", padx=(16, 4))
        speed_box = ttk.Combobox(
            player_controls,
            textvariable=self.playback_rate_var,
            state="readonly",
            values=("0.5", "1.0", "1.5", "2.0", "3.0", "4.0", "5.0"),
            width=6,
        )
        speed_box.pack(side="left")
        speed_box.bind("<<ComboboxSelected>>", lambda _event: self.apply_playback_rate())

        ttk.Label(player_controls, text="音量").pack(side="left", padx=(12, 4))
        volume_scale = ttk.Scale(
            player_controls,
            from_=0,
            to=100,
            orient="horizontal",
            variable=self.volume_var,
            command=self.on_volume_change,
        )
        volume_scale.pack(side="left", padx=(0, 4), fill="x", expand=True)
        ttk.Label(player_controls, textvariable=self.volume_var, width=4).pack(side="left")
        ttk.Label(player_controls, textvariable=self.video_status_var).pack(side="right")

        local_progress_row = ttk.Frame(video_wrap)
        local_progress_row.pack(fill="x", padx=8, pady=(0, 8))
        self.local_progress_canvas = tk.Canvas(local_progress_row, height=72, bg="#f8f8f8")
        self.local_progress_canvas.pack(side="left", fill="x", expand=True)
        self.local_progress_canvas.bind("<ButtonPress-1>", self.on_press_local_progress)
        self.local_progress_canvas.bind("<B1-Motion>", self.on_drag_local_progress)
        self.local_progress_canvas.bind("<ButtonRelease-1>", self.on_release_local_progress)
        self.local_progress_canvas.bind("<Configure>", lambda _event: self._schedule_local_progress_redraw())
        ttk.Button(local_progress_row, text="暂停并定位", command=self.pause_and_locate_segment).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(local_progress_row, text="编辑起点", command=lambda: self.start_boundary_edit("start")).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(local_progress_row, text="编辑终点", command=lambda: self.start_boundary_edit("end")).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(local_progress_row, text="确认合并", command=self.confirm_merge_range).pack(side="left", padx=(6, 0))
        ttk.Label(local_progress_row, textvariable=self.edit_state_var).pack(side="left", padx=(10, 0))

        timeline_wrap = ttk.LabelFrame(self, text="热度时间轴")
        timeline_wrap.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(timeline_wrap, textvariable=self.timeline_info_var).pack(anchor="w", padx=8, pady=(6, 2))
        ttk.Label(timeline_wrap, textvariable=self.duration_summary_var).pack(anchor="w", padx=8, pady=(0, 2))
        self.heat_canvas = tk.Canvas(timeline_wrap, height=150, bg="#fafafa")
        self.heat_canvas.pack(fill="x", padx=8, pady=(0, 8))
        self.heat_canvas.bind("<Button-1>", self.on_click_heatline)
        self.heat_canvas.bind("<Configure>", lambda _event: self._schedule_heatline_redraw())

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self.segments_tree = ttk.Treeview(
            tree_frame,
            columns=("id", "start", "end", "heat", "label"),
            show="headings",
            height=12,
        )

        for col, width in (("id", 60), ("start", 130), ("end", 130), ("heat", 80), ("label", 120)):
            self.segments_tree.heading(col, text=col, command=lambda c=col: self.on_sort_by_column(c))
            self.segments_tree.column(col, width=width, anchor="center")

        self._refresh_tree_header_texts()

        self.segments_tree.pack(side="left", fill="both", expand=True)
        self.segments_tree.bind("<Double-1>", lambda _event: self.play_selected_segment())
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.segments_tree.yview)
        self.segments_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        self.bind_all("i", lambda _event: self.mark_selected("interesting"))
        self.bind_all("u", lambda _event: self.mark_selected("uninteresting"))
        self.bind_all("<Left>", lambda _event: self.nudge_selected_boundary(-self._edit_step_sec))
        self.bind_all("<Right>", lambda _event: self.nudge_selected_boundary(self._edit_step_sec))

    def load_task(self, task_id: int) -> None:
        self.current_task_id = int(task_id)
        task = self.review_service.get_task(self.current_task_id)
        self.task_info_var.set(
            f"当前任务: #{task.id} | {task.video_name} | {task.speaker_id} | {task.status}"
        )
        self.total_duration_sec = self.review_service.get_task_duration_sec(self.current_task_id)
        self._all_segments_cache = sorted(self.review_service.list_all_segments(self.current_task_id), key=lambda x: x.start_sec)
        default_window = min(20000.0, self.total_duration_sec) if self.total_duration_sec > 0 else 20000.0
        self.window_duration_var.set(f"{default_window:.0f}")
        self.window_start_var.set("0")
        self.current_seek_sec = 0.0
        self.clear_merge_range()
        self.seek_info_var.set(f"当前定位: {self._format_time(0.0)}")
        self.stop_candidate_segments()
        self._load_media_for_current_task()
        self._schedule_heatline_redraw()
        self.refresh_candidates()
        self._schedule_local_progress_redraw()

    def parse_window_params(self) -> tuple[float, float, float]:
        try:
            window_duration = float(self.window_duration_var.get())
            window_start = float(self.window_start_var.get())
        except ValueError as exc:
            raise ValueError("窗口参数必须是数字") from exc

        if window_duration <= 0:
            raise ValueError("窗口时长必须大于0")
        if window_start < 0:
            raise ValueError("窗口起点不能小于0")

        if self.total_duration_sec > 0:
            window_duration = min(window_duration, self.total_duration_sec)

        max_start = max(0.0, self.total_duration_sec - window_duration)
        clamped_start = min(window_start, max_start)
        window_end = min(self.total_duration_sec, clamped_start + window_duration)
        return window_duration, clamped_start, window_end

    def reload_window(self) -> None:
        if self.current_task_id is None:
            return
        try:
            _window_duration, window_start, _window_end = self.parse_window_params()
        except ValueError as error:
            messagebox.showerror("窗口参数错误", str(error))
            return

        self.window_duration_var.set(f"{_window_duration:.0f}")
        self.window_start_var.set(f"{window_start:.0f}")
        self.refresh_candidates()
        self._schedule_heatline_redraw()

    def prev_window(self) -> None:
        if self.current_task_id is None:
            return
        try:
            window_duration, window_start, _window_end = self.parse_window_params()
        except ValueError as error:
            messagebox.showerror("窗口参数错误", str(error))
            return

        self.window_start_var.set(f"{max(0.0, window_start - window_duration):.0f}")
        self.reload_window()

    def next_window(self) -> None:
        if self.current_task_id is None:
            return
        try:
            window_duration, window_start, _window_end = self.parse_window_params()
        except ValueError as error:
            messagebox.showerror("窗口参数错误", str(error))
            return

        max_start = max(0.0, self.total_duration_sec - window_duration)
        self.window_start_var.set(f"{min(max_start, window_start + window_duration):.0f}")
        self.reload_window()

    def get_thresholds(self) -> tuple[float, float]:
        try:
            min_t = float(self.threshold_min_var.get())
            max_t = float(self.threshold_max_var.get())
        except ValueError as exc:
            raise ValueError("阈值必须是数字") from exc
        if not (0.0 <= min_t <= max_t <= 1.0):
            raise ValueError("阈值必须满足 0 <= min <= max <= 1")
        return min_t, max_t

    def refresh_candidates(self) -> None:
        if self.current_task_id is None:
            return
        try:
            min_t, max_t = self.get_thresholds()
            _window_duration, window_start, window_end = self.parse_window_params()
        except ValueError as error:
            messagebox.showerror("阈值错误", str(error))
            return

        rows = self.review_service.list_window_candidates(
            self.current_task_id,
            min_t,
            max_t,
            window_start_sec=window_start,
            window_end_sec=window_end,
        )
        self._all_segments_cache = sorted(self.review_service.list_all_segments(self.current_task_id), key=lambda x: x.start_sec)
        self._window_candidates = list(rows)
        self._tree_sort_column = "start"
        self._tree_sort_desc = False
        self._refresh_tree_header_texts()
        self._render_segments_table()
        self._refresh_duration_summary(min_t, max_t)
        self._set_timeline_info(window_start, window_end)
        self._schedule_heatline_redraw()
        self._schedule_local_progress_redraw()

    def mark_selected(self, label: str) -> None:
        if self.current_task_id is None:
            return
        selected = self.segments_tree.selection()
        if not selected:
            return
        segment_id = int(self.segments_tree.item(selected[0], "values")[0])
        self.review_service.mark_segment(self.current_task_id, segment_id, label)
        self.refresh_candidates()
        self._schedule_heatline_redraw()
        self.on_task_refresh()

    def smart_mark(self) -> None:
        if self._smart_mark_thread is not None and self._smart_mark_thread.is_alive():
            messagebox.showinfo("处理中", "智能标记正在执行，请稍候")
            return
        if self.current_task_id is None:
            return
        try:
            min_t, max_t = self.get_thresholds()
            high_offset = float(self.smart_high_offset_var.get())
            low_offset = float(self.smart_low_offset_var.get())
        except ValueError as error:
            messagebox.showerror("参数错误", str(error))
            return
        if high_offset < 0 or low_offset < 0:
            messagebox.showerror("参数错误", "智能偏移必须是非负数")
            return

        task_id = self.current_task_id
        self._show_smart_mark_loading("正在智能标记，请稍候...")
        self._set_smart_mark_enabled(False)

        def worker() -> None:
            try:
                result = self.review_service.smart_mark_segments(
                    task_id,
                    base_threshold=min_t,
                    max_threshold=max_t,
                    high_offset=high_offset,
                    low_offset=low_offset,
                )
                self._smart_mark_result_queue.put(("ok", task_id, result))
            except Exception as exc:
                self._smart_mark_result_queue.put(("error", task_id, exc))

        self._smart_mark_thread = Thread(target=worker, daemon=True)
        self._smart_mark_thread.start()
        self._poll_smart_mark_result()

    def _poll_smart_mark_result(self) -> None:
        try:
            status, task_id, payload = self._smart_mark_result_queue.get_nowait()
        except Empty:
            self._smart_mark_poll_after_id = self.after(150, self._poll_smart_mark_result)
            return

        self._smart_mark_poll_after_id = None
        self._smart_mark_thread = None
        self._hide_smart_mark_loading()
        self._set_smart_mark_enabled(True)

        if self.current_task_id != task_id:
            return

        if status == "ok":
            interesting_count, uninteresting_count, unchanged_count = payload
            self.refresh_candidates()
            self._schedule_heatline_redraw()
            self.on_task_refresh()
            messagebox.showinfo(
                "智能标记完成",
                (
                    f"有趣: {interesting_count} 条\n"
                    f"无趣: {uninteresting_count} 条\n"
                    f"保持不变: {unchanged_count} 条"
                ),
            )
            return

        messagebox.showerror("智能标记失败", str(payload))

    def clear_all_marks(self) -> None:
        if self.current_task_id is None:
            return
        if not messagebox.askyesno("确认", "确认清除该任务的全部标记吗？"):
            return

        cleared_count = self.review_service.clear_all_marks(self.current_task_id)
        self.refresh_candidates()
        self._schedule_heatline_redraw()
        self.on_task_refresh()
        messagebox.showinfo("已完成", f"已清除 {cleared_count} 条标记")

    def on_back_to_tasks_clicked(self) -> None:
        self.stop_playback_for_navigation()
        if self.on_back_to_tasks is not None:
            self.on_back_to_tasks()

    def _show_smart_mark_loading(self, text: str) -> None:
        if self._smart_mark_loading_dialog is not None and self._smart_mark_loading_dialog.winfo_exists():
            return

        dialog = tk.Toplevel(self)
        dialog.title("处理中")
        dialog.geometry("380x120")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=text).pack(fill="x", pady=(0, 10))

        progress = ttk.Progressbar(frame, mode="indeterminate", length=320)
        progress.pack(fill="x")
        progress.start(10)

        dialog.grab_set()
        self._smart_mark_loading_dialog = dialog
        self._smart_mark_loading_progress = progress

    def _hide_smart_mark_loading(self) -> None:
        if self._smart_mark_loading_progress is not None:
            self._smart_mark_loading_progress.stop()
        self._smart_mark_loading_progress = None

        if self._smart_mark_loading_dialog is not None and self._smart_mark_loading_dialog.winfo_exists():
            self._smart_mark_loading_dialog.grab_release()
            self._smart_mark_loading_dialog.destroy()
        self._smart_mark_loading_dialog = None

    def _set_smart_mark_enabled(self, enabled: bool) -> None:
        if self.smart_mark_button is None:
            return
        self.smart_mark_button.config(state="normal" if enabled else "disabled")

    def undo_last(self) -> None:
        if self.current_task_id is None:
            return
        if not self.review_service.undo_last_mark(self.current_task_id):
            messagebox.showinfo("提示", "没有可撤销的标记")
            return
        self.refresh_candidates()
        self._schedule_heatline_redraw()
        self.on_task_refresh()

    def complete_review(self) -> None:
        if self.current_task_id is None:
            return
        self.review_service.complete_review(self.current_task_id)
        self.on_task_refresh()
        task = self.review_service.get_task(self.current_task_id)
        self.task_info_var.set(
            f"当前任务: #{task.id} | {task.video_name} | {task.speaker_id} | {task.status}"
        )

    def save_profile(self) -> None:
        if self.current_task_id is None:
            return
        try:
            min_t, max_t = self.get_thresholds()
        except ValueError as error:
            messagebox.showerror("阈值错误", str(error))
            return
        task = self.review_service.get_task(self.current_task_id)
        self.review_service.save_threshold_profile(task.speaker_id, min_t, max_t)
        messagebox.showinfo("已保存", f"已保存 {task.speaker_id} 的默认阈值档案")

    def load_profile(self) -> None:
        if self.current_task_id is None:
            return
        task = self.review_service.get_task(self.current_task_id)
        profile = self.review_service.get_threshold_profile(task.speaker_id)
        if profile is None:
            messagebox.showinfo("提示", f"{task.speaker_id} 暂无默认档案")
            return
        self.threshold_min_var.set(f"{profile.min_threshold:.2f}")
        self.threshold_max_var.set(f"{profile.max_threshold:.2f}")
        self.refresh_candidates()

    def play_from_current_seek(self) -> None:
        self.play_video_at(self.current_seek_sec)

    def play_selected_segment(self) -> None:
        selected = self.segments_tree.selection()
        if not selected:
            return
        values = self.segments_tree.item(selected[0], "values")
        segment_id = int(values[0])
        segment = self._find_cached_segment(segment_id)
        if segment is None:
            return
        self.play_video_at(float(segment.start_sec))

    def play_video_at(self, seek_sec: float) -> None:
        if self.current_task_id is None or not self._ensure_player_ready():
            return

        if self.candidate_loop_var.get():
            self.candidate_loop_var.set(False)
        self._cancel_candidate_tick()
        self._player.play()
        self.after(120, lambda: self._player.set_time(int(max(0.0, seek_sec) * 1000)))
        self.current_seek_sec = max(0.0, seek_sec)
        current_text = self._format_time(self.current_seek_sec, include_tenths=True)
        self.seek_info_var.set(f"当前定位: {current_text}")
        self.video_status_var.set(f"播放器状态: 播放中 ({current_text})")
        self._schedule_local_progress_redraw()

    def on_click_heatline(self, event) -> None:
        if self.total_duration_sec <= 0:
            return
        width = max(self.heat_canvas.winfo_width(), 1)
        axis_left = 48
        axis_right = max(axis_left + 1, width - 16)
        x = min(max(event.x, axis_left), axis_right)
        ratio = (x - axis_left) / (axis_right - axis_left)
        self.current_seek_sec = ratio * self.total_duration_sec
        current_text = self._format_time(self.current_seek_sec, include_tenths=True)
        self.seek_info_var.set(f"当前定位: {current_text}")
        if self._player_available:
            self.video_status_var.set(f"播放器状态: 已定位到 {current_text}")
        self._schedule_local_progress_redraw()

    def on_press_local_progress(self, event) -> None:
        if self._merge_start_sec is not None and self._merge_end_sec is not None:
            handle = self._hit_test_merge_handle(event.x)
            if handle is not None:
                self._dragging_boundary = handle
                self._edit_boundary = handle
                self.edit_state_var.set(f"编辑: 拖拽{'起点' if handle == 'start' else '终点'}")
                self._schedule_local_progress_redraw()
                return
        self._dragging_boundary = None
        self._seek_from_local_progress_x(event.x)

    def on_drag_local_progress(self, event) -> None:
        if self._dragging_boundary is None:
            return
        sec_value = self._local_x_to_sec(event.x)
        self._set_merge_boundary(self._dragging_boundary, sec_value)

    def on_release_local_progress(self, _event) -> None:
        self._dragging_boundary = None

    def _seek_from_local_progress_x(self, x_value: float) -> None:
        if not self._local_track_items:
            return
        range_start, range_end = self._local_track_range
        if range_end <= range_start:
            return
        axis_left, axis_right = self._local_axis_bounds()
        x = min(max(x_value, axis_left), axis_right)
        ratio = (x - axis_left) / (axis_right - axis_left)
        self.current_seek_sec = range_start + ratio * (range_end - range_start)
        if self._player is not None and self._player_available:
            try:
                self._player.set_time(int(self.current_seek_sec * 1000))
            except Exception:
                pass
        self.seek_info_var.set(f"当前定位: {self._format_time(self.current_seek_sec, include_tenths=True)}")
        self._select_segment_by_time(self.current_seek_sec)
        self._schedule_local_progress_redraw()

    def on_toggle_candidate_loop(self) -> None:
        if self.candidate_loop_var.get():
            self.play_candidate_segments()
            return
        self.stop_candidate_segments(show_message=False)

    def start_boundary_edit(self, boundary: str) -> None:
        if boundary not in ("start", "end"):
            return
        selected = self.segments_tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先在下方列表选择一个片段")
            return
        values = self.segments_tree.item(selected[0], "values")
        segment = self._find_cached_segment(int(values[0]))
        if segment is None:
            return
        if self._merge_start_sec is None or self._merge_end_sec is None:
            self._merge_start_sec = segment.start_sec
            self._merge_end_sec = segment.end_sec
        self._edit_boundary = boundary
        self.edit_state_var.set(
            f"编辑: {'起点' if boundary == 'start' else '终点'} (左右键微调 {self._edit_step_sec:.1f}s)"
        )
        self._schedule_local_progress_redraw()

    def nudge_selected_boundary(self, delta_sec: float) -> None:
        if self._edit_boundary is None:
            return
        if self._merge_start_sec is None or self._merge_end_sec is None:
            self._merge_start_sec = self.current_seek_sec
            self._merge_end_sec = self.current_seek_sec + self._drag_min_gap_sec
        current_value = self._merge_start_sec if self._edit_boundary == "start" else self._merge_end_sec
        self._set_merge_boundary(self._edit_boundary, current_value + delta_sec)

    def clear_merge_range(self) -> None:
        self._edit_boundary = None
        self._dragging_boundary = None
        self._merge_start_sec = None
        self._merge_end_sec = None
        self.edit_state_var.set("编辑: 未启用")

    def confirm_merge_range(self) -> None:
        if self.current_task_id is None:
            return
        if self._merge_start_sec is None or self._merge_end_sec is None:
            messagebox.showinfo("提示", "请先选择片段并设置合并区间")
            return
        affected_count, max_heat = self.review_service.merge_candidate_heat_in_range(
            self.current_task_id,
            self._merge_start_sec,
            self._merge_end_sec,
        )
        if affected_count <= 0:
            messagebox.showinfo("提示", "合并区间内没有可更新的候选片段")
            return
        self._all_segments_cache = sorted(self.review_service.list_all_segments(self.current_task_id), key=lambda x: x.start_sec)
        self.refresh_candidates()
        self.clear_merge_range()
        self._schedule_local_progress_redraw()
        messagebox.showinfo("已完成", f"已更新 {affected_count} 个片段热度为 {max_heat:.3f}")

    def pause_and_locate_segment(self) -> None:
        if self._player is not None and self._player_available:
            try:
                self._player.set_pause(1)
            except Exception:
                try:
                    self._player.pause()
                except Exception:
                    pass
        self.video_status_var.set("播放器状态: 暂停")

        segment = self._find_segment_by_time(self.current_seek_sec, self._window_candidates)
        if segment is None:
            messagebox.showinfo("提示", "当前位置不在当前筛选片段中")
            return
        item_id = str(segment.id)
        if not self.segments_tree.exists(item_id):
            return
        self.segments_tree.selection_set(item_id)
        self.segments_tree.focus(item_id)
        self.segments_tree.see(item_id)
        self._schedule_local_progress_redraw()

    def _init_player(self) -> None:
        if self._player_ready and self._player is not None:
            return
        self._player_init_attempted = True

        if self.video_panel is None:
            self._player_error_detail = "video panel not ready"
            return
        self._prepare_vlc_runtime()
        try:
            import vlc
        except OSError as error:
            self._player_error_detail = self._format_vlc_import_error(error)
            self.video_status_var.set("播放器状态: VLC加载失败")
            self._player_available = False
            return
        except Exception as error:
            self._player_error_detail = self._format_vlc_import_error(error)
            self.video_status_var.set("播放器状态: 未检测到 python-vlc 或 libVLC")
            self._player_available = False
            return

        try:
            self._vlc = vlc
            self._vlc_instance = vlc.Instance("--quiet", "--no-video-title-show")
            self._player = self._vlc_instance.media_player_new()
            self.update_idletasks()
            handle = self.video_panel.winfo_id()
            self._player.set_hwnd(handle)
            self._player_available = True
            self._player_ready = True
            self._player_error_detail = ""
            self.video_status_var.set("播放器状态: 已就绪")
            self._apply_volume()
            self._bind_player_time_update()
        except Exception as error:
            self._player_available = False
            self._player_error_detail = str(error)
            self.video_status_var.set(f"播放器状态: 初始化失败 ({error})")

    def _prepare_vlc_runtime(self) -> None:
        if os.name != "nt":
            return

        candidates = self._find_vlc_install_dirs_windows()
        python_bitness = self._python_bitness()
        selected_folder = None
        fallback_folder = None

        for folder in candidates:
            if not os.path.exists(os.path.join(folder, "libvlc.dll")):
                continue
            folder_bitness = self._guess_vlc_dir_bitness(folder)
            if folder_bitness == python_bitness and selected_folder is None:
                selected_folder = folder
                break
            if fallback_folder is None:
                fallback_folder = folder

        chosen = selected_folder or fallback_folder
        if chosen is None:
            self._selected_vlc_dir = None
            return

        self._selected_vlc_dir = chosen
        path_items = os.environ.get("PATH", "")
        if chosen not in path_items:
            os.environ["PATH"] = f"{chosen};{path_items}"
        add_dll_directory = getattr(os, "add_dll_directory", None)
        if add_dll_directory is not None:
            try:
                add_dll_directory(chosen)
            except Exception:
                pass
        plugin_path = os.path.join(chosen, "plugins")
        if os.path.isdir(plugin_path) and not os.environ.get("VLC_PLUGIN_PATH"):
            os.environ["VLC_PLUGIN_PATH"] = plugin_path

    def _python_bitness(self) -> str:
        return "64" if struct.calcsize("P") * 8 == 64 else "32"

    def _guess_vlc_dir_bitness(self, folder: str) -> str:
        normalized = folder.lower()
        if "(x86)" in normalized:
            return "32"
        if "program files" in normalized:
            return "64"
        return "unknown"

    def _format_vlc_import_error(self, error: Exception) -> str:
        base = f"import vlc failed: {error}"
        if os.name != "nt":
            return base

        winerror = getattr(error, "winerror", None)
        if winerror != 193:
            return base

        python_bitness = self._python_bitness()
        chosen = self._selected_vlc_dir or "未定位到VLC安装目录"
        expected = "64位VLC" if python_bitness == "64" else "32位VLC"
        return (
            f"{base} (WinError 193: 架构不匹配). "
            f"当前Python为{python_bitness}位, 需要安装{expected}, 或切换到匹配位数的Python。"
            f" 当前VLC目录: {chosen}"
        )

    def _find_vlc_install_dirs_windows(self) -> list[str]:
        python_bitness = self._python_bitness()
        default_candidates: list[str] = [
            r"C:\Program Files\VideoLAN\VLC",
            r"C:\Program Files (x86)\VideoLAN\VLC",
        ]
        if python_bitness == "32":
            default_candidates.reverse()

        candidates: list[str] = []

        env_vlc_dir = os.environ.get("VLC_DIR", "").strip()
        if env_vlc_dir:
            candidates.append(env_vlc_dir)

        try:
            import winreg

            registry_keys = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\VideoLAN\VLC"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\VideoLAN\VLC"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\VideoLAN\VLC"),
            ]
            for root, sub_key in registry_keys:
                try:
                    with winreg.OpenKey(root, sub_key) as key:
                        install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
                        if isinstance(install_dir, str) and install_dir:
                            candidates.append(install_dir)
                except OSError:
                    continue
        except Exception:
            pass

        candidates.extend(default_candidates)

        deduped: list[str] = []
        for path in candidates:
            normalized = os.path.normpath(path)
            if normalized not in deduped:
                deduped.append(normalized)

        matched = [path for path in deduped if self._guess_vlc_dir_bitness(path) == python_bitness]
        unknown = [path for path in deduped if self._guess_vlc_dir_bitness(path) == "unknown"]
        mismatched = [path for path in deduped if self._guess_vlc_dir_bitness(path) not in (python_bitness, "unknown")]
        if matched or unknown:
            return matched + unknown + mismatched
        return deduped

    def _ensure_player_ready(self, show_error: bool = True) -> bool:
        if not self._player_init_attempted or not self._player_ready:
            self._init_player()
        if self._player_available and self._player_ready and self._player is not None:
            return True
        if show_error:
            extra = f"\n\n详细信息: {self._player_error_detail}" if self._player_error_detail else ""
            messagebox.showerror(
                "播放器不可用",
                "未检测到可用VLC播放器，请确认已安装 VLC 桌面版并执行 pip install python-vlc。" + extra,
            )
        return False

    def _load_media_for_current_task(self) -> None:
        if self.current_task_id is None or not self._ensure_player_ready(show_error=False):
            return
        try:
            task = self.review_service.get_task(self.current_task_id)
            media = self._vlc_instance.media_new(task.video_path)
            self._player.set_media(media)
            self.video_status_var.set("播放器状态: 媒体已加载")
        except Exception as error:
            messagebox.showerror("加载失败", f"无法加载视频: {error}")

    def play_current(self) -> None:
        if not self._ensure_player_ready():
            return
        if self.current_task_id is None:
            return
        self._player.play()
        self.video_status_var.set("播放器状态: 播放中")

    def pause_current(self) -> None:
        if not self._ensure_player_ready():
            return
        self._player.pause()
        self.video_status_var.set("播放器状态: 暂停")

    def stop_current(self) -> None:
        if not self._ensure_player_ready():
            return
        self.candidate_loop_var.set(False)
        self.stop_candidate_segments(show_message=False)
        self._player.stop()
        self.video_status_var.set("播放器状态: 停止")

    def stop_playback_for_navigation(self) -> None:
        self.candidate_loop_var.set(False)
        self.stop_candidate_segments(show_message=False)
        if self._player is None:
            self.video_status_var.set("播放器状态: 停止")
            return
        try:
            self._player.stop()
        except Exception:
            pass
        self.video_status_var.set("播放器状态: 停止")

    def apply_playback_rate(self) -> None:
        if not self._ensure_player_ready():
            return
        try:
            rate = float(self.playback_rate_var.get())
        except ValueError:
            return
        self._player.set_rate(rate)

    def on_volume_change(self, _value=None) -> None:
        self._apply_volume()

    def _apply_volume(self) -> None:
        if self._player is None:
            return
        try:
            volume = int(self.volume_var.get())
        except (ValueError, tk.TclError):
            volume = 70
        volume = max(0, min(100, volume))
        self.volume_var.set(volume)
        try:
            self._player.audio_set_volume(volume)
        except Exception:
            pass

    def play_candidate_segments(self) -> None:
        if self.current_task_id is None:
            return
        if not self._ensure_player_ready():
            return
        try:
            min_t, max_t = self.get_thresholds()
            _window_duration, window_start, window_end = self.parse_window_params()
        except ValueError as error:
            self.candidate_loop_var.set(False)
            messagebox.showerror("参数错误", str(error))
            return

        segments = self.review_service.list_window_candidates(
            self.current_task_id,
            min_t,
            max_t,
            window_start_sec=window_start,
            window_end_sec=window_end,
        )
        if not segments:
            self.candidate_loop_var.set(False)
            messagebox.showinfo("提示", "当前时间窗没有候选片段")
            return

        self._candidate_segments = sorted(segments, key=lambda x: x.start_sec)
        self._candidate_index = 0
        self._play_candidate_index(0)

    def _play_candidate_index(self, index: int) -> None:
        if index < 0 or index >= len(self._candidate_segments):
            self.stop_candidate_segments(show_message=False)
            self.video_status_var.set("播放器状态: 候选轮播完成")
            return

        segment = self._candidate_segments[index]
        self._candidate_index = index
        self._player.play()
        self.after(120, lambda: self._player.set_time(int(segment.start_sec * 1000)))
        self.video_status_var.set(
            f"播放器状态: 候选片段 {index + 1}/{len(self._candidate_segments)} "
            f"({self._format_time(segment.start_sec, include_tenths=True)}-"
            f"{self._format_time(segment.end_sec, include_tenths=True)})"
        )
        self._schedule_candidate_tick()

    def _schedule_candidate_tick(self) -> None:
        self._cancel_candidate_tick()
        self._candidate_tick_after_id = self.after(160, self._candidate_tick)

    def _cancel_candidate_tick(self) -> None:
        if self._candidate_tick_after_id is not None:
            try:
                self.after_cancel(self._candidate_tick_after_id)
            except Exception:
                pass
            self._candidate_tick_after_id = None

    def _candidate_tick(self) -> None:
        if not self._candidate_segments or self._candidate_index < 0:
            return
        segment = self._candidate_segments[self._candidate_index]
        current_ms = self._player.get_time()
        if current_ms < 0:
            self._schedule_candidate_tick()
            return
        current_sec = current_ms / 1000.0
        if current_sec >= segment.end_sec - 0.05:
            next_index = self._candidate_index + 1
            if next_index >= len(self._candidate_segments):
                self.stop_candidate_segments(show_message=False)
                self.video_status_var.set("播放器状态: 候选轮播完成")
                return
            self._play_candidate_index(next_index)
            return
        self._schedule_candidate_tick()

    def stop_candidate_segments(self, show_message: bool = False) -> None:
        self._cancel_candidate_tick()
        had_loop = bool(self._candidate_segments)
        self._candidate_segments = []
        self._candidate_index = -1
        if self.candidate_loop_var.get():
            self.candidate_loop_var.set(False)
        if show_message and had_loop:
            messagebox.showinfo("提示", "候选轮播已停止")

    def _bind_player_time_update(self) -> None:
        if not self._player_available:
            return

        def tick() -> None:
            if not self.winfo_exists():
                return
            if self._player is not None:
                current_ms = self._player.get_time()
                if current_ms >= 0:
                    self.current_seek_sec = current_ms / 1000.0
                    self.seek_info_var.set(
                        f"当前定位: {self._format_time(self.current_seek_sec, include_tenths=True)}"
                    )
                    self._schedule_local_progress_redraw()
            self._player_time_after_id = self.after(300, tick)

        tick()

    def _schedule_heatline_redraw(self) -> None:
        if self.heat_canvas is None or not self.winfo_exists():
            return
        if self._heatline_redraw_after_id is not None:
            try:
                self.after_cancel(self._heatline_redraw_after_id)
            except Exception:
                pass
        self._heatline_redraw_after_id = self.after(40, self.draw_heatline)

    def _schedule_local_progress_redraw(self) -> None:
        if self.local_progress_canvas is None or not self.winfo_exists():
            return
        if self._local_progress_redraw_after_id is not None:
            try:
                self.after_cancel(self._local_progress_redraw_after_id)
            except Exception:
                pass
        self._local_progress_redraw_after_id = self.after(40, self.draw_local_progress)

    def _on_destroy(self, event) -> None:
        if event.widget is not self:
            return
        if self._smart_mark_poll_after_id is not None:
            try:
                self.after_cancel(self._smart_mark_poll_after_id)
            except Exception:
                pass
            self._smart_mark_poll_after_id = None
        self._hide_smart_mark_loading()
        self._cancel_candidate_tick()
        if self._heatline_redraw_after_id is not None:
            try:
                self.after_cancel(self._heatline_redraw_after_id)
            except Exception:
                pass
            self._heatline_redraw_after_id = None
        if self._local_progress_redraw_after_id is not None:
            try:
                self.after_cancel(self._local_progress_redraw_after_id)
            except Exception:
                pass
            self._local_progress_redraw_after_id = None
        if self._player_time_after_id is not None:
            try:
                self.after_cancel(self._player_time_after_id)
            except Exception:
                pass
            self._player_time_after_id = None
        if self._player is not None:
            try:
                self._player.stop()
                self._player.release()
            except Exception:
                pass
            self._player = None
        if self._vlc_instance is not None:
            try:
                self._vlc_instance.release()
            except Exception:
                pass
            self._vlc_instance = None

    def draw_heatline(self) -> None:
        self._heatline_redraw_after_id = None
        if self.heat_canvas is None or not self.winfo_exists():
            return

        self.heat_canvas.delete("all")
        if self.current_task_id is None:
            return

        try:
            _window_duration, window_start, window_end = self.parse_window_params()
        except ValueError:
            return

        try:
            min_t, _max_t = self.get_thresholds()
        except ValueError:
            min_t = 0.0

        segments = self.review_service.list_window_segments(self.current_task_id, window_start, window_end)
        width = max(self.heat_canvas.winfo_width(), 1)
        height = max(self.heat_canvas.winfo_height(), 1)
        axis_left = 48
        axis_right = max(axis_left + 1, width - 16)
        axis_y = height - 18

        bar_top = 14
        bar_bottom = 26
        self.heat_canvas.create_rectangle(axis_left, bar_top, axis_right, bar_bottom, fill="#e8e8e8", outline="")

        self.heat_canvas.create_line(axis_left, axis_y, axis_right, axis_y, fill="#555")
        ticks = 6
        for i in range(ticks + 1):
            ratio = i / ticks
            x = axis_left + ratio * (axis_right - axis_left)
            self.heat_canvas.create_line(x, axis_y - 3, x, axis_y + 3, fill="#666")
            label_sec = ratio * self.total_duration_sec
            self.heat_canvas.create_text(x, axis_y + 11, text=self._format_time(label_sec), anchor="n", fill="#444")

        if self.total_duration_sec > 0:
            ws_x = axis_left + (window_start / self.total_duration_sec) * (axis_right - axis_left)
            we_x = axis_left + (window_end / self.total_duration_sec) * (axis_right - axis_left)
            self.heat_canvas.create_rectangle(ws_x, axis_y - 6, we_x, axis_y + 6, outline="#2196f3")
            self.heat_canvas.create_rectangle(ws_x, bar_top, we_x, bar_bottom, fill="#90caf9", outline="")

            seek_x = axis_left + (self.current_seek_sec / self.total_duration_sec) * (axis_right - axis_left)
            self.heat_canvas.create_line(seek_x, 10, seek_x, axis_y - 8, fill="#ff5722", dash=(3, 2))
            self.heat_canvas.create_text(seek_x, 8, text=self._format_time(self.current_seek_sec), anchor="s", fill="#ff5722")

        self._set_timeline_info(window_start, window_end)

        if not segments:
            self.heat_canvas.create_text(
                (axis_left + axis_right) / 2,
                (axis_y - 12) / 2,
                text="当前时间窗暂无热度片段",
                fill="#777",
            )
            return

        for segment in segments:
            if self.total_duration_sec <= 0:
                continue
            x0 = axis_left + (segment.start_sec / self.total_duration_sec) * (axis_right - axis_left)
            x1 = axis_left + (segment.end_sec / self.total_duration_sec) * (axis_right - axis_left)
            y1 = axis_y - 8
            y0 = y1 - (segment.heat_score * (height - 38))
            color = "#4caf50" if segment.heat_score >= min_t else "#9e9e9e"
            self.heat_canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")

    def draw_local_progress(self) -> None:
        self._local_progress_redraw_after_id = None
        if self.local_progress_canvas is None or not self.winfo_exists():
            return
        canvas = self.local_progress_canvas
        canvas.delete("all")
        if not self._all_segments_cache:
            canvas.create_text(10, 24, text="暂无片段数据", fill="#777", anchor="w")
            return

        try:
            min_t, max_t = self.get_thresholds()
        except ValueError:
            min_t, max_t = 0.0, 1.0

        focus_index = self._find_focus_segment_index(self._all_segments_cache, self.current_seek_sec)
        if focus_index is None:
            canvas.create_text(10, 24, text="当前位置不在片段范围内", fill="#777", anchor="w")
            return
        focus_segment = self._all_segments_cache[focus_index]

        self._local_track_items, self._local_track_range = self._build_local_track(
            self._all_segments_cache,
            focus_index,
            min_t,
            max_t,
        )
        range_start, range_end = self._local_track_range
        if range_end <= range_start:
            return

        width = max(canvas.winfo_width(), 1)
        axis_left = 20
        axis_right = max(axis_left + 1, width - 20)
        bar_top = 18
        bar_bottom = 42
        span = range_end - range_start

        loop_hint = "候选轮播已关闭"
        if self._candidate_segments:
            loop_hint = f"候选轮播 {self._candidate_index + 1}/{len(self._candidate_segments)}"
        status = "筛选内" if min_t <= focus_segment.heat_score <= max_t else "筛选外"
        canvas.create_text(
            axis_left,
            6,
            text=(
                f"当前片段 #{focus_segment.id} {self._format_time(focus_segment.start_sec, include_tenths=True)}"
                f"-{self._format_time(focus_segment.end_sec, include_tenths=True)} | {status} | {loop_hint}"
            ),
            anchor="nw",
            fill="#444",
        )

        for item in self._local_track_items:
            x0 = axis_left + ((item.start_sec - range_start) / span) * (axis_right - axis_left)
            x1 = axis_left + ((item.end_sec - range_start) / span) * (axis_right - axis_left)
            if item.is_gap:
                fill = "#ffffff"
                outline = "#c7c7c7"
            else:
                fill = "#4caf50" if item.in_threshold else "#cfcfcf"
                outline = ""
            canvas.create_rectangle(x0, bar_top, x1, bar_bottom, fill=fill, outline=outline)

        if self._merge_start_sec is not None and self._merge_end_sec is not None:
            merge_start = min(self._merge_start_sec, self._merge_end_sec)
            merge_end = max(self._merge_start_sec, self._merge_end_sec)
            merge_x0 = axis_left + ((max(merge_start, range_start) - range_start) / span) * (axis_right - axis_left)
            merge_x1 = axis_left + ((min(merge_end, range_end) - range_start) / span) * (axis_right - axis_left)
            canvas.create_rectangle(merge_x0, bar_top - 4, merge_x1, bar_bottom + 4, outline="#ff9800", width=2)
            start_color = "#ef6c00" if self._dragging_boundary == "start" else "#ff9800"
            end_color = "#ef6c00" if self._dragging_boundary == "end" else "#ff9800"
            canvas.create_oval(merge_x0 - 4, bar_top - 8, merge_x0 + 4, bar_top, fill=start_color, outline="")
            canvas.create_oval(merge_x1 - 4, bar_top - 8, merge_x1 + 4, bar_top, fill=end_color, outline="")

        self._draw_local_ticks(canvas, axis_left, axis_right, bar_bottom, range_start, span)

        seek_x = axis_left + ((min(max(self.current_seek_sec, range_start), range_end) - range_start) / span) * (
            axis_right - axis_left
        )
        canvas.create_line(seek_x, 10, seek_x, 56, fill="#f44336", width=2)
        canvas.create_text(seek_x, 58, text=self._format_time(self.current_seek_sec, include_tenths=True), anchor="n", fill="#f44336")

    def _draw_local_ticks(
        self,
        canvas: tk.Canvas,
        axis_left: float,
        axis_right: float,
        bar_bottom: float,
        range_start: float,
        span: float,
    ) -> None:
        if span <= 0:
            return
        epsilon = 1e-4
        seen_labels: set[str] = set()
        for item in self._local_track_items:
            if item.is_gap or not item.in_threshold:
                continue
            for boundary in (item.start_sec, item.end_sec):
                x = axis_left + ((boundary - range_start) / span) * (axis_right - axis_left)
                canvas.create_line(x, bar_bottom + 1, x, bar_bottom + 7, fill="#2e7d32")
                bucket = f"{round(boundary / epsilon) * epsilon:.4f}"
                if bucket in seen_labels:
                    continue
                seen_labels.add(bucket)
                canvas.create_text(x, bar_bottom + 9, text=self._format_time(boundary), anchor="n", fill="#2e7d32")

    @staticmethod
    def _find_focus_segment_index(segments, seek_sec: float) -> int | None:
        if not segments:
            return None
        epsilon = 1e-6
        for i, segment in enumerate(segments):
            is_last = i == len(segments) - 1
            in_range = segment.start_sec - epsilon <= seek_sec < segment.end_sec - epsilon
            if in_range or (is_last and seek_sec <= segment.end_sec + epsilon):
                return i
            if seek_sec < segment.start_sec:
                return max(0, i - 1)
        return len(segments) - 1

    @staticmethod
    def _build_local_track(segments, focus_index: int, min_t: float, max_t: float) -> tuple[list[TrackItem], tuple[float, float]]:
        start_idx = max(0, focus_index - 4)
        end_idx = min(len(segments), focus_index + 5)
        selected = segments[start_idx:end_idx]
        if not selected:
            return [], (0.0, 0.0)

        result: list[TrackItem] = []
        gap_count = 0
        epsilon = 1e-4
        prev_end = None
        for segment in selected:
            if prev_end is not None and segment.start_sec > prev_end + epsilon and gap_count < 10:
                result.append(TrackItem(start_sec=prev_end, end_sec=segment.start_sec, is_gap=True))
                gap_count += 1
            result.append(
                TrackItem(
                    start_sec=segment.start_sec,
                    end_sec=segment.end_sec,
                    is_gap=False,
                    in_threshold=min_t <= segment.heat_score <= max_t,
                )
            )
            prev_end = segment.end_sec
        return result, (selected[0].start_sec, selected[-1].end_sec)

    @staticmethod
    def _find_segment_by_time(seek_sec: float, segments):
        epsilon = 1e-6
        for segment in segments:
            if segment.start_sec - epsilon <= seek_sec <= segment.end_sec + epsilon:
                return segment
        return None

    def _select_segment_by_time(self, seek_sec: float) -> None:
        segment = self._find_segment_by_time(seek_sec, self._window_candidates)
        if segment is None:
            return
        item_id = str(segment.id)
        if not self.segments_tree.exists(item_id):
            return
        self.segments_tree.selection_set(item_id)
        self.segments_tree.focus(item_id)
        self.segments_tree.see(item_id)

    def _local_axis_bounds(self) -> tuple[float, float]:
        width = max(self.local_progress_canvas.winfo_width(), 1)
        axis_left = 20
        axis_right = max(axis_left + 1, width - 20)
        return axis_left, axis_right

    def _local_x_to_sec(self, x_value: float) -> float:
        range_start, range_end = self._local_track_range
        if range_end <= range_start:
            return range_start
        axis_left, axis_right = self._local_axis_bounds()
        x = min(max(x_value, axis_left), axis_right)
        ratio = (x - axis_left) / (axis_right - axis_left)
        return range_start + ratio * (range_end - range_start)

    def _sec_to_local_x(self, sec_value: float) -> float:
        range_start, range_end = self._local_track_range
        axis_left, axis_right = self._local_axis_bounds()
        if range_end <= range_start:
            return axis_left
        clamped = min(max(sec_value, range_start), range_end)
        ratio = (clamped - range_start) / (range_end - range_start)
        return axis_left + ratio * (axis_right - axis_left)

    def _hit_test_merge_handle(self, x_value: float) -> str | None:
        if self._merge_start_sec is None or self._merge_end_sec is None:
            return None
        start_x = self._sec_to_local_x(min(self._merge_start_sec, self._merge_end_sec))
        end_x = self._sec_to_local_x(max(self._merge_start_sec, self._merge_end_sec))
        distance_start = abs(x_value - start_x)
        distance_end = abs(x_value - end_x)
        if distance_start > self._drag_hit_px and distance_end > self._drag_hit_px:
            return None
        return "start" if distance_start <= distance_end else "end"

    def _set_merge_boundary(self, boundary: str, target_sec: float) -> None:
        range_start, range_end = self._local_track_range
        if range_end <= range_start:
            return
        if self._merge_start_sec is None or self._merge_end_sec is None:
            self._merge_start_sec = range_start
            self._merge_end_sec = min(range_end, range_start + self._drag_min_gap_sec)

        if boundary == "start":
            limit = self._merge_end_sec - self._drag_min_gap_sec
            self._merge_start_sec = max(range_start, min(target_sec, limit))
        else:
            limit = self._merge_start_sec + self._drag_min_gap_sec
            self._merge_end_sec = min(range_end, max(target_sec, limit))

        self.edit_state_var.set(
            "编辑区间: "
            f"{self._format_time(self._merge_start_sec, include_tenths=True)} - "
            f"{self._format_time(self._merge_end_sec, include_tenths=True)}"
        )
        self._schedule_local_progress_redraw()

    def _find_cached_segment(self, segment_id: int):
        for segment in self._window_candidates:
            if segment.id == segment_id:
                return segment
        return None

    def _set_timeline_info(self, window_start: float, window_end: float) -> None:
        total = max(0.0, self.total_duration_sec)
        window_len = max(0.0, window_end - window_start)
        ratio = (window_len / total * 100.0) if total > 0 else 0.0
        self.timeline_info_var.set(
            f"窗口 {self._format_time(window_start)} - {self._format_time(window_end)} "
            f"(时长 {self._format_time(window_len)}) / 总时长 {self._format_time(total)} | 覆盖 {ratio:.1f}%"
        )

    def _refresh_duration_summary(self, min_threshold: float, max_threshold: float) -> None:
        if self.current_task_id is None:
            self.duration_summary_var.set("筛选时长 00:00 (0.0%) | 有趣时长 00:00 (0.0%)")
            return
        filtered_sec, interesting_sec = self.review_service.get_duration_stats(
            self.current_task_id,
            min_threshold,
            max_threshold,
        )
        total = max(0.0, self.total_duration_sec)
        filtered_ratio = (filtered_sec / total * 100.0) if total > 0 else 0.0
        interesting_ratio = (interesting_sec / total * 100.0) if total > 0 else 0.0
        self.duration_summary_var.set(
            f"筛选时长 {self._format_time(filtered_sec)} ({filtered_ratio:.1f}%) | "
            f"有趣时长 {self._format_time(interesting_sec)} ({interesting_ratio:.1f}%)"
        )

    def _format_time(self, seconds: float, include_tenths: bool = False) -> str:
        safe = max(0.0, float(seconds))
        whole = int(safe)
        frac = int((safe - whole) * 10)
        hours = whole // 3600
        minutes = (whole % 3600) // 60
        secs = whole % 60
        if hours > 0:
            base = f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            base = f"{minutes:02d}:{secs:02d}"
        if include_tenths:
            return f"{base}.{frac}"
        return base

    def on_sort_by_column(self, column: str) -> None:
        if column == self._tree_sort_column:
            self._tree_sort_desc = not self._tree_sort_desc
        else:
            self._tree_sort_column = column
            self._tree_sort_desc = False
        self._refresh_tree_header_texts()
        self._render_segments_table()

    def _refresh_tree_header_texts(self) -> None:
        if self.segments_tree is None:
            return
        title_map = {
            "id": "ID",
            "start": "开始时间",
            "end": "结束时间",
            "heat": "热度",
            "label": "标记",
        }
        for col, title in title_map.items():
            arrow = ""
            if col == self._tree_sort_column:
                arrow = " ▼" if self._tree_sort_desc else " ▲"
            self.segments_tree.heading(col, text=f"{title}{arrow}", command=lambda c=col: self.on_sort_by_column(c))

    def _render_segments_table(self) -> None:
        if self.segments_tree is None:
            return
        key_map = {
            "id": lambda s: s.id,
            "start": lambda s: s.start_sec,
            "end": lambda s: s.end_sec,
            "heat": lambda s: s.heat_score,
            "label": lambda s: s.current_label or "",
        }
        sort_key = key_map.get(self._tree_sort_column, key_map["start"])
        sorted_rows = sorted(self._window_candidates, key=sort_key, reverse=self._tree_sort_desc)

        self.segments_tree.delete(*self.segments_tree.get_children())
        for segment in sorted_rows:
            self.segments_tree.insert(
                "",
                "end",
                iid=str(segment.id),
                values=(
                    segment.id,
                    self._format_time(segment.start_sec, include_tenths=True),
                    self._format_time(segment.end_sec, include_tenths=True),
                    f"{segment.heat_score:.3f}",
                    segment.current_label or "",
                ),
            )
