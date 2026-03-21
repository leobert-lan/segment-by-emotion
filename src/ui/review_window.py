import tkinter as tk
import os
import struct
from tkinter import messagebox, ttk

from src.services.review_service import ReviewService


class ReviewWindow(ttk.Frame):
    def __init__(self, master: tk.Widget, review_service: ReviewService, on_task_refresh) -> None:
        super().__init__(master)
        self.review_service = review_service
        self.on_task_refresh = on_task_refresh
        self.current_task_id: int | None = None

        self.task_var = tk.StringVar()
        self.threshold_min_var = tk.StringVar(value="0.40")
        self.threshold_max_var = tk.StringVar(value="1.00")
        self.window_duration_var = tk.StringVar(value="600")
        self.window_start_var = tk.StringVar(value="0")
        self.timeline_info_var = tk.StringVar(value="时间窗 0s - 0s / 总时长 0s")
        self.seek_info_var = tk.StringVar(value="当前定位: 0s")

        self.task_combo = None
        self.segments_tree = None
        self.heat_canvas = None
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

        self._candidate_segments = []
        self._candidate_index = -1
        self._candidate_tick_after_id: str | None = None

        self.video_panel = None
        self.video_status_var = tk.StringVar(value="播放器状态: 未初始化")
        self.playback_rate_var = tk.StringVar(value="1.0")

        self._build_layout()
        self.refresh_tasks()
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _build_layout(self) -> None:
        controls = ttk.LabelFrame(self, text="任务与筛选")
        controls.pack(fill="x", padx=10, pady=8)

        task_row = ttk.Frame(controls)
        task_row.pack(fill="x", padx=8, pady=(6, 4))
        ttk.Label(task_row, text="任务").pack(side="left")
        self.task_combo = ttk.Combobox(task_row, textvariable=self.task_var, state="readonly", width=70)
        self.task_combo.pack(side="left", fill="x", expand=True, padx=8)
        self.task_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_select_task())

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
        ttk.Button(threshold_row, text="应用筛选", command=self.refresh_candidates).pack(side="left", padx=(8, 4))
        ttk.Button(threshold_row, text="加载说话人档案", command=self.load_profile).pack(side="left", padx=4)
        ttk.Button(threshold_row, text="保存说话人档案", command=self.save_profile).pack(side="left", padx=4)

        action_row = ttk.Frame(controls)
        action_row.pack(fill="x", padx=8, pady=(4, 8))
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
        ttk.Button(player_controls, text="仅播放候选片段", command=self.play_candidate_segments).pack(side="left", padx=4)
        ttk.Button(player_controls, text="停止候选轮播", command=self.stop_candidate_segments).pack(side="left", padx=4)

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
        ttk.Label(player_controls, textvariable=self.video_status_var).pack(side="right")

        timeline_wrap = ttk.LabelFrame(self, text="热度时间轴")
        timeline_wrap.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(timeline_wrap, textvariable=self.timeline_info_var).pack(anchor="w", padx=8, pady=(6, 2))
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

        for col, width in (("id", 60), ("start", 90), ("end", 90), ("heat", 80), ("label", 100)):
            self.segments_tree.heading(col, text=col)
            self.segments_tree.column(col, width=width, anchor="center")

        self.segments_tree.pack(side="left", fill="both", expand=True)
        self.segments_tree.bind("<Double-1>", lambda _event: self.play_selected_segment())
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.segments_tree.yview)
        self.segments_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        self.bind_all("i", lambda _event: self.mark_selected("interesting"))
        self.bind_all("u", lambda _event: self.mark_selected("uninteresting"))

    def refresh_tasks(self) -> None:
        tasks = self.review_service.list_tasks()
        choices = [f"{task.id} | {task.video_name} | {task.speaker_id} | {task.status}" for task in tasks]
        self.task_combo["values"] = choices
        if choices and not self.task_var.get():
            self.task_combo.current(0)
            self.on_select_task()

    def on_select_task(self) -> None:
        raw = self.task_var.get().strip()
        if not raw:
            return
        self.current_task_id = int(raw.split("|", maxsplit=1)[0].strip())
        self.total_duration_sec = self.review_service.get_task_duration_sec(self.current_task_id)
        self.window_start_var.set("0")
        self.current_seek_sec = 0.0
        self.seek_info_var.set("当前定位: 0s")
        self.stop_candidate_segments()
        self._load_media_for_current_task()
        self._schedule_heatline_redraw()
        self.refresh_candidates()

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

        self.timeline_info_var.set(
            f"时间窗 {window_start:.0f}s - {window_end:.0f}s / 总时长 {self.total_duration_sec:.0f}s"
        )

        rows = self.review_service.list_window_candidates(
            self.current_task_id,
            min_t,
            max_t,
            window_start_sec=window_start,
            window_end_sec=window_end,
        )
        self.segments_tree.delete(*self.segments_tree.get_children())
        for segment in rows:
            self.segments_tree.insert(
                "",
                "end",
                values=(
                    segment.id,
                    f"{segment.start_sec:.1f}",
                    f"{segment.end_sec:.1f}",
                    f"{segment.heat_score:.3f}",
                    segment.current_label or "",
                ),
            )
        self._schedule_heatline_redraw()

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
        self.refresh_tasks()

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
        start_sec = float(values[1])
        self.play_video_at(start_sec)

    def play_video_at(self, seek_sec: float) -> None:
        if self.current_task_id is None or not self._ensure_player_ready():
            return

        self._cancel_candidate_tick()
        self._player.play()
        self.after(120, lambda: self._player.set_time(int(max(0.0, seek_sec) * 1000)))
        self.current_seek_sec = max(0.0, seek_sec)
        self.seek_info_var.set(f"当前定位: {self.current_seek_sec:.1f}s")
        self.video_status_var.set(f"播放器状态: 播放中 ({self.current_seek_sec:.1f}s)")

    def on_click_heatline(self, event) -> None:
        if self.total_duration_sec <= 0:
            return
        width = max(self.heat_canvas.winfo_width(), 1)
        axis_left = 48
        axis_right = max(axis_left + 1, width - 16)
        x = min(max(event.x, axis_left), axis_right)
        ratio = (x - axis_left) / (axis_right - axis_left)
        self.current_seek_sec = ratio * self.total_duration_sec
        self.seek_info_var.set(f"当前定位: {self.current_seek_sec:.1f}s")
        if self._player_available:
            self.video_status_var.set(f"播放器状态: 已定位到 {self.current_seek_sec:.1f}s")

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
        self.stop_candidate_segments()
        self._player.stop()
        self.video_status_var.set("播放器状态: 停止")

    def apply_playback_rate(self) -> None:
        if not self._ensure_player_ready():
            return
        try:
            rate = float(self.playback_rate_var.get())
        except ValueError:
            return
        self._player.set_rate(rate)

    def play_candidate_segments(self) -> None:
        if self.current_task_id is None:
            return
        if not self._ensure_player_ready():
            return
        try:
            min_t, max_t = self.get_thresholds()
            _window_duration, window_start, window_end = self.parse_window_params()
        except ValueError as error:
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
            f"播放器状态: 候选片段 {index + 1}/{len(self._candidate_segments)} ({segment.start_sec:.1f}s-{segment.end_sec:.1f}s)"
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
                    self.seek_info_var.set(f"当前定位: {self.current_seek_sec:.1f}s")
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

    def _on_destroy(self, event) -> None:
        if event.widget is not self:
            return
        self._cancel_candidate_tick()
        if self._heatline_redraw_after_id is not None:
            try:
                self.after_cancel(self._heatline_redraw_after_id)
            except Exception:
                pass
            self._heatline_redraw_after_id = None
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

        segments = self.review_service.list_window_segments(self.current_task_id, window_start, window_end)
        width = max(self.heat_canvas.winfo_width(), 1)
        height = max(self.heat_canvas.winfo_height(), 1)
        axis_left = 48
        axis_right = max(axis_left + 1, width - 16)
        axis_y = height - 18

        self.heat_canvas.create_line(axis_left, axis_y, axis_right, axis_y, fill="#555")
        ticks = 6
        for i in range(ticks + 1):
            ratio = i / ticks
            x = axis_left + ratio * (axis_right - axis_left)
            self.heat_canvas.create_line(x, axis_y - 3, x, axis_y + 3, fill="#666")
            label_sec = ratio * self.total_duration_sec
            self.heat_canvas.create_text(x, axis_y + 11, text=f"{label_sec:.0f}s", anchor="n", fill="#444")

        if self.total_duration_sec > 0:
            ws_x = axis_left + (window_start / self.total_duration_sec) * (axis_right - axis_left)
            we_x = axis_left + (window_end / self.total_duration_sec) * (axis_right - axis_left)
            self.heat_canvas.create_rectangle(ws_x, axis_y - 6, we_x, axis_y + 6, outline="#2196f3")

            seek_x = axis_left + (self.current_seek_sec / self.total_duration_sec) * (axis_right - axis_left)
            self.heat_canvas.create_line(seek_x, 10, seek_x, axis_y - 8, fill="#ff5722", dash=(3, 2))

        self.timeline_info_var.set(
            f"时间窗 {window_start:.0f}s - {window_end:.0f}s / 总时长 {self.total_duration_sec:.0f}s"
        )

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
            color = "#4caf50"
            if segment.current_label == "uninteresting":
                color = "#9e9e9e"
            elif segment.current_label == "interesting":
                color = "#ff9800"
            self.heat_canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")

