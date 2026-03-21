import tkinter as tk
from queue import Empty, Queue
from threading import Thread
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from src.infra.repositories import TaskRepository
from src.services.ingest_service import TaskIngestService
from src.services.review_service import ReviewService
from src.services.stage3_stub import Stage3PipelineStub
from src.ui.review_window import ReviewWindow


class MainWindow(tk.Tk):
    def __init__(
        self,
        task_repository: TaskRepository,
        ingest_service: TaskIngestService,
        review_service: ReviewService,
        stage3_stub: Stage3PipelineStub,
    ) -> None:
        super().__init__()
        self.title("Segment By Motion - MVP")
        self.geometry("1360x900")
        self.minsize(1200, 780)

        self.task_repository = task_repository
        self.ingest_service = ingest_service
        self.review_service = review_service
        self.stage3_stub = stage3_stub

        self.video_path_var = tk.StringVar()
        self.speaker_id_var = tk.StringVar(value="speaker_001")
        self.create_task_button: ttk.Button | None = None

        self.loading_dialog: tk.Toplevel | None = None
        self.loading_progress: ttk.Progressbar | None = None
        self._create_task_thread: Thread | None = None
        self._create_task_result_queue: Queue = Queue()
        self._create_task_poll_after_id: str | None = None

        self.tasks_tree = ttk.Treeview(
            self,
            columns=("id", "video", "speaker", "status", "segments"),
            show="headings",
            height=12,
        )

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        self.task_page = ttk.Frame(notebook)
        notebook.add(self.task_page, text="任务管理")
        self._build_task_page()

        self.review_page = ReviewWindow(notebook, review_service=self.review_service, on_task_refresh=self.refresh_tasks)
        notebook.add(self.review_page, text="Review")

        self.refresh_tasks()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_task_page(self) -> None:
        top = ttk.Frame(self.task_page)
        top.pack(fill="x", padx=12, pady=10)

        ttk.Label(top, text="视频文件").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.video_path_var, width=80).grid(row=0, column=1, padx=6, sticky="ew")
        ttk.Button(top, text="浏览", command=self.pick_video).grid(row=0, column=2, padx=4)

        ttk.Label(top, text="说话人ID").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(top, textvariable=self.speaker_id_var, width=20).grid(row=1, column=1, sticky="w", padx=6)

        self.create_task_button = ttk.Button(top, text="创建任务并计算热度", command=self.create_task)
        self.create_task_button.grid(row=1, column=2, padx=4)
        ttk.Button(top, text="刷新列表", command=self.refresh_tasks).grid(row=1, column=3, padx=4)
        ttk.Button(top, text="发送到第三阶段(Stub)", command=self.send_to_stage3).grid(row=1, column=4, padx=4)

        top.columnconfigure(1, weight=1)

        table_wrap = ttk.Frame(self.task_page)
        table_wrap.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        for col, width in (("id", 60), ("video", 420), ("speaker", 120), ("status", 150), ("segments", 100)):
            self.tasks_tree.heading(col, text=col)
            self.tasks_tree.column(col, width=width, anchor="center")
        self.tasks_tree.column("video", anchor="w")

        self.tasks_tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tasks_tree.yview)
        self.tasks_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

    def pick_video(self) -> None:
        path = filedialog.askopenfilename(
            title="选择视频",
            filetypes=[("Video", "*.mp4 *.mkv *.avi *.mov"), ("All files", "*.*")],
        )
        if path:
            self.video_path_var.set(path)

    def create_task(self) -> None:
        if self._create_task_thread is not None and self._create_task_thread.is_alive():
            messagebox.showinfo("任务进行中", "当前正在计算热度，请等待完成")
            return

        video_path = self.video_path_var.get().strip()
        speaker_id = self.speaker_id_var.get().strip()
        if not video_path:
            messagebox.showerror("参数错误", "请先选择视频文件")
            return
        if not speaker_id:
            messagebox.showerror("参数错误", "请填写说话人ID")
            return
        if not Path(video_path).exists():
            messagebox.showerror("文件不存在", video_path)
            return

        self._show_loading("正在提取音频并计算热度，请稍候...")
        self._set_create_task_enabled(False)

        def worker() -> None:
            try:
                task = self.ingest_service.create_task_and_run_stage1(video_path=video_path, speaker_id=speaker_id)
                self._create_task_result_queue.put(("ok", task))
            except Exception as exc:
                self._create_task_result_queue.put(("error", exc))

        self._create_task_thread = Thread(target=worker, daemon=True)
        self._create_task_thread.start()
        self._poll_create_task_result()

    def _poll_create_task_result(self) -> None:
        try:
            status, payload = self._create_task_result_queue.get_nowait()
        except Empty:
            self._create_task_poll_after_id = self.after(150, self._poll_create_task_result)
            return

        self._create_task_poll_after_id = None
        self._create_task_thread = None
        self._hide_loading()
        self._set_create_task_enabled(True)

        if status == "ok":
            task = payload
            self.refresh_tasks()
            self.review_page.refresh_tasks()
            messagebox.showinfo("任务已创建", f"Task {task.id} 已完成阶段一分段与热度计算")
            return

        messagebox.showerror("计算失败", f"创建任务失败：{payload}")

    def _show_loading(self, text: str) -> None:
        if self.loading_dialog is not None and self.loading_dialog.winfo_exists():
            return

        dialog = tk.Toplevel(self)
        dialog.title("处理中")
        dialog.geometry("420x120")
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=text).pack(fill="x", pady=(0, 10))

        progress = ttk.Progressbar(frame, mode="indeterminate", length=380)
        progress.pack(fill="x")
        progress.start(10)

        dialog.grab_set()
        self.loading_dialog = dialog
        self.loading_progress = progress

    def _hide_loading(self) -> None:
        if self.loading_progress is not None:
            self.loading_progress.stop()
        self.loading_progress = None

        if self.loading_dialog is not None and self.loading_dialog.winfo_exists():
            self.loading_dialog.grab_release()
            self.loading_dialog.destroy()
        self.loading_dialog = None

    def _set_create_task_enabled(self, enabled: bool) -> None:
        if self.create_task_button is None:
            return
        state = "normal" if enabled else "disabled"
        self.create_task_button.config(state=state)

    def refresh_tasks(self) -> None:
        self.tasks_tree.delete(*self.tasks_tree.get_children())
        for task in self.task_repository.list_tasks():
            segment_count = self.task_repository.count_segments(task.id)
            self.tasks_tree.insert(
                "",
                "end",
                values=(task.id, task.video_name, task.speaker_id, task.status, segment_count),
            )

    def selected_task_id(self) -> int | None:
        selected = self.tasks_tree.selection()
        if not selected:
            return None
        return int(self.tasks_tree.item(selected[0], "values")[0])

    def send_to_stage3(self) -> None:
        task_id = self.selected_task_id()
        if task_id is None:
            messagebox.showinfo("提示", "请先在列表中选择任务")
            return
        msg = self.stage3_stub.enqueue(task_id)
        messagebox.showinfo("第三阶段", msg)

    def on_close(self) -> None:
        if self._create_task_poll_after_id is not None:
            self.after_cancel(self._create_task_poll_after_id)
            self._create_task_poll_after_id = None
        self._hide_loading()
        self.destroy()

