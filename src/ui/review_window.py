import tkinter as tk
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

        self.task_combo = None
        self.segments_tree = None
        self.heat_canvas = None

        self._build_layout()
        self.refresh_tasks()

    def _build_layout(self) -> None:
        controls = ttk.Frame(self)
        controls.pack(fill="x", padx=10, pady=8)

        self.task_combo = ttk.Combobox(controls, textvariable=self.task_var, state="readonly", width=50)

        ttk.Label(controls, text="任务").grid(row=0, column=0, sticky="w")
        self.task_combo.grid(row=0, column=1, columnspan=4, sticky="ew", padx=6)
        self.task_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_select_task())

        ttk.Label(controls, text="阈值最小").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(controls, textvariable=self.threshold_min_var, width=8).grid(row=1, column=1, sticky="w")
        ttk.Label(controls, text="阈值最大").grid(row=1, column=2, sticky="w")
        ttk.Entry(controls, textvariable=self.threshold_max_var, width=8).grid(row=1, column=3, sticky="w")

        ttk.Button(controls, text="应用筛选", command=self.refresh_candidates).grid(row=1, column=4, padx=4)
        ttk.Button(controls, text="加载说话人档案", command=self.load_profile).grid(row=2, column=1, pady=6)
        ttk.Button(controls, text="保存说话人档案", command=self.save_profile).grid(row=2, column=2, pady=6)
        ttk.Button(controls, text="撤销上次标记", command=self.undo_last).grid(row=2, column=3, pady=6)
        ttk.Button(controls, text="完成Review", command=self.complete_review).grid(row=2, column=4, pady=6)

        controls.columnconfigure(1, weight=1)

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
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.segments_tree.yview)
        self.segments_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=10, pady=6)
        ttk.Button(buttons, text="标记有趣 (I)", command=lambda: self.mark_selected("interesting")).pack(side="left", padx=4)
        ttk.Button(buttons, text="标记无趣 (U)", command=lambda: self.mark_selected("uninteresting")).pack(side="left", padx=4)

        self.heat_canvas = tk.Canvas(self, height=140, bg="#fafafa")
        self.heat_canvas.pack(fill="x", padx=10, pady=(2, 10))

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
        self.draw_heatline()
        self.refresh_candidates()

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
        except ValueError as error:
            messagebox.showerror("阈值错误", str(error))
            return

        rows = self.review_service.list_candidates(self.current_task_id, min_t, max_t)
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

    def mark_selected(self, label: str) -> None:
        if self.current_task_id is None:
            return
        selected = self.segments_tree.selection()
        if not selected:
            return
        segment_id = int(self.segments_tree.item(selected[0], "values")[0])
        self.review_service.mark_segment(self.current_task_id, segment_id, label)
        self.refresh_candidates()
        self.draw_heatline()
        self.on_task_refresh()

    def undo_last(self) -> None:
        if self.current_task_id is None:
            return
        if not self.review_service.undo_last_mark(self.current_task_id):
            messagebox.showinfo("提示", "没有可撤销的标记")
            return
        self.refresh_candidates()
        self.draw_heatline()
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

    def draw_heatline(self) -> None:
        self.heat_canvas.delete("all")
        if self.current_task_id is None:
            return

        segments = self.review_service.list_all_segments(self.current_task_id)
        if not segments:
            return

        width = max(self.heat_canvas.winfo_width(), 1)
        height = max(self.heat_canvas.winfo_height(), 1)
        bar_width = max(1, width / len(segments))

        for idx, segment in enumerate(segments):
            x0 = idx * bar_width
            x1 = x0 + bar_width
            y1 = height
            y0 = y1 - (segment.heat_score * (height - 20))
            color = "#4caf50"
            if segment.current_label == "uninteresting":
                color = "#9e9e9e"
            elif segment.current_label == "interesting":
                color = "#ff9800"
            self.heat_canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")

