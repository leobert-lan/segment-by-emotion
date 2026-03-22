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
        self.minsize(1200, 1300)

        self.task_repository = task_repository
        self.ingest_service = ingest_service
        self.review_service = review_service
        self.stage3_stub = stage3_stub

        self.video_path_var = tk.StringVar()
        self.import_dir_var = tk.StringVar()
        self.speaker_id_var = tk.StringVar(value="speaker_001")
        self.batch_generate_heat_var = tk.BooleanVar(value=True)
        self.create_task_button: ttk.Button | None = None
        self.batch_import_button: ttk.Button | None = None

        self.loading_dialog: tk.Toplevel | None = None
        self.loading_progress: ttk.Progressbar | None = None
        self._create_task_thread: Thread | None = None
        self._create_task_result_queue: Queue = Queue()
        self._create_task_poll_after_id: str | None = None
        self._batch_import_thread: Thread | None = None
        self._batch_import_result_queue: Queue = Queue()
        self._batch_import_poll_after_id: str | None = None

        self.tasks_tree: ttk.Treeview | None = None

        self.page_container = ttk.Frame(self)
        self.page_container.pack(fill="both", expand=True)

        self.task_page = ttk.Frame(self.page_container)
        self._build_task_page()

        self.review_page = ReviewWindow(
            self.page_container,
            review_service=self.review_service,
            on_task_refresh=self.refresh_tasks,
            on_back_to_tasks=self.show_task_page,
        )

        self.task_page.pack(fill="both", expand=True)

        self.refresh_tasks()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_task_page(self) -> None:
        top = ttk.Frame(self.task_page)
        top.pack(fill="x", padx=12, pady=10)

        ttk.Label(top, text="视频文件").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.video_path_var, width=80).grid(row=0, column=1, padx=6, sticky="ew")
        ttk.Button(top, text="浏览", command=self.pick_video).grid(row=0, column=2, padx=4)
        self.create_task_button = ttk.Button(top, text="创建单个任务并计算热度", command=self.create_task)
        self.create_task_button.grid(row=0, column=3, padx=4)

        ttk.Label(top, text="批量目录").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(top, textvariable=self.import_dir_var, width=80).grid(row=1, column=1, padx=6, sticky="ew")
        ttk.Button(top, text="选择目录", command=self.pick_import_directory).grid(row=1, column=2, padx=4)
        ttk.Checkbutton(top, text="导入后生成热度", variable=self.batch_generate_heat_var).grid(row=1, column=3, padx=4, sticky="w")

        ttk.Label(top, text="说话人ID").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(top, textvariable=self.speaker_id_var, width=20).grid(row=2, column=1, sticky="w", padx=6)

        self.batch_import_button = ttk.Button(top, text="批量导入目录", command=self.batch_import_directory)
        self.batch_import_button.grid(row=2, column=2, padx=4)
        ttk.Button(top, text="刷新列表", command=self.refresh_tasks).grid(row=2, column=3, padx=4)
        ttk.Button(top, text="删除所选任务", command=self.delete_selected_task).grid(row=2, column=4, padx=4)
        ttk.Button(top, text="发送到第三阶段(Stub)", command=self.send_to_stage3).grid(row=2, column=5, padx=4)
        ttk.Label(top, text="提示: 双击任务进入 Review").grid(row=3, column=1, columnspan=5, sticky="w", padx=6, pady=(2, 0))

        top.columnconfigure(1, weight=1)

        table_wrap = ttk.Frame(self.task_page)
        table_wrap.pack(fill="both", expand=True, padx=12, pady=(4, 12))

        self.tasks_tree = ttk.Treeview(
            table_wrap,
            columns=("id", "video", "speaker", "status", "segments"),
            show="headings",
            height=12,
        )

        for col, width in (("id", 60), ("video", 420), ("speaker", 120), ("status", 150), ("segments", 100)):
            self.tasks_tree.heading(col, text=col)
            self.tasks_tree.column(col, width=width, anchor="center")
        self.tasks_tree.column("video", anchor="w")

        self.tasks_tree.pack(side="left", fill="both", expand=True)
        self.tasks_tree.bind("<Double-1>", self.open_review_for_selected_task)
        scrollbar = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tasks_tree.yview)
        self.tasks_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

    def show_task_page(self) -> None:
        self.review_page.stop_playback_for_navigation()
        self.review_page.pack_forget()
        self.task_page.pack(fill="both", expand=True)

    def show_review_page(self, task_id: int) -> None:
        self.task_page.pack_forget()
        self.review_page.pack(fill="both", expand=True)
        self.review_page.load_task(task_id)

    def pick_video(self) -> None:
        path = filedialog.askopenfilename(
            title="选择视频",
            filetypes=[("Video", "*.mp4 *.mkv *.avi *.mov"), ("All files", "*.*")],
        )
        if path:
            self.video_path_var.set(path)

    def pick_import_directory(self) -> None:
        path = filedialog.askdirectory(title="选择批量导入目录")
        if path:
            self.import_dir_var.set(path)

    def create_task(self) -> None:
        if (self._create_task_thread is not None and self._create_task_thread.is_alive()) or (
            self._batch_import_thread is not None and self._batch_import_thread.is_alive()
        ):
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
        self._set_action_buttons_enabled(False)

        def worker() -> None:
            try:
                task = self.ingest_service.create_task_and_run_stage1(video_path=video_path, speaker_id=speaker_id)
                self._create_task_result_queue.put(("ok", task))
            except Exception as exc:
                self._create_task_result_queue.put(("error", exc))

        self._create_task_thread = Thread(target=worker, daemon=True)
        self._create_task_thread.start()
        self._poll_create_task_result()

    def batch_import_directory(self) -> None:
        if (self._create_task_thread is not None and self._create_task_thread.is_alive()) or (
            self._batch_import_thread is not None and self._batch_import_thread.is_alive()
        ):
            messagebox.showinfo("任务进行中", "已有导入或计算任务在执行，请稍候")
            return

        directory_path = self.import_dir_var.get().strip()
        speaker_id = self.speaker_id_var.get().strip()
        if not directory_path:
            messagebox.showerror("参数错误", "请先选择批量导入目录")
            return
        if not speaker_id:
            messagebox.showerror("参数错误", "请填写说话人ID")
            return
        if not Path(directory_path).exists():
            messagebox.showerror("目录不存在", directory_path)
            return

        generate_heat_data = bool(self.batch_generate_heat_var.get())
        loading_text = "正在批量导入并计算热度，请稍候..." if generate_heat_data else "正在批量导入任务，请稍候..."
        self._show_loading(loading_text)
        self._set_action_buttons_enabled(False)

        def worker() -> None:
            try:
                result = self.ingest_service.batch_import_directory(
                    directory_path=directory_path,
                    speaker_id=speaker_id,
                    generate_heat_data=generate_heat_data,
                )
                self._batch_import_result_queue.put(("ok", result))
            except Exception as exc:
                self._batch_import_result_queue.put(("error", exc))

        self._batch_import_thread = Thread(target=worker, daemon=True)
        self._batch_import_thread.start()
        self._poll_batch_import_result()

    def _poll_create_task_result(self) -> None:
        try:
            status, payload = self._create_task_result_queue.get_nowait()
        except Empty:
            self._create_task_poll_after_id = self.after(150, self._poll_create_task_result)
            return

        self._create_task_poll_after_id = None
        self._create_task_thread = None
        self._hide_loading()
        self._set_action_buttons_enabled(True)

        if status == "ok":
            task = payload
            self.refresh_tasks()
            messagebox.showinfo("任务已创建", f"Task {task.id} 已完成阶段一分段与热度计算")
            return

        messagebox.showerror("计算失败", f"创建任务失败：{payload}")

    def _poll_batch_import_result(self) -> None:
        try:
            status, payload = self._batch_import_result_queue.get_nowait()
        except Empty:
            self._batch_import_poll_after_id = self.after(150, self._poll_batch_import_result)
            return

        self._batch_import_poll_after_id = None
        self._batch_import_thread = None
        self._hide_loading()
        self._set_action_buttons_enabled(True)

        if status == "ok":
            result = payload
            self.refresh_tasks()
            failure_lines = [f"- {path}: {reason}" for path, reason in result.failed[:5]]
            failure_text = "\n".join(failure_lines)
            suffix = ""
            if result.failed:
                suffix = f"\n失败 {len(result.failed)} 个:\n{failure_text}"
            messagebox.showinfo(
                "批量导入完成",
                (
                    f"扫描视频: {result.scanned_count} 个\n"
                    f"成功导入: {result.imported_count} 个\n"
                    f"已生成热度: {result.heat_generated_count} 个"
                    f"{suffix}"
                ),
            )
            return

        messagebox.showerror("批量导入失败", str(payload))

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

    def _set_action_buttons_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        if self.create_task_button is not None:
            self.create_task_button.config(state=state)
        if self.batch_import_button is not None:
            self.batch_import_button.config(state=state)

    def refresh_tasks(self) -> None:
        if self.tasks_tree is None:
            return
        self.tasks_tree.delete(*self.tasks_tree.get_children())
        for task in self.task_repository.list_tasks():
            segment_count = self.task_repository.count_segments(task.id)
            self.tasks_tree.insert(
                "",
                "end",
                values=(task.id, task.video_name, task.speaker_id, task.status, segment_count),
            )

    def selected_task_id(self) -> int | None:
        if self.tasks_tree is None:
            return None
        selected = self.tasks_tree.selection()
        if not selected:
            return None
        return int(self.tasks_tree.item(selected[0], "values")[0])

    def open_review_for_selected_task(self, _event=None) -> None:
        task_id = self.selected_task_id()
        if task_id is None:
            return
        self.show_review_page(task_id)

    def delete_selected_task(self) -> None:
        task_id = self.selected_task_id()
        if task_id is None:
            messagebox.showinfo("提示", "请先在列表中选择任务")
            return

        confirmed = messagebox.askyesno("确认删除", f"确认删除任务 {task_id} 及其所有分段和标记记录吗？")
        if not confirmed:
            return

        deleted = self.task_repository.delete_task(task_id)
        if deleted <= 0:
            messagebox.showerror("删除失败", f"未找到任务 {task_id}")
            return

        self.refresh_tasks()
        messagebox.showinfo("删除完成", f"任务 {task_id} 已删除")

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
        if self._batch_import_poll_after_id is not None:
            self.after_cancel(self._batch_import_poll_after_id)
            self._batch_import_poll_after_id = None
        self._hide_loading()
        self.destroy()

