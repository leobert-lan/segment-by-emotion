import tkinter as tk
from queue import Empty, Queue
from threading import Thread
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Optional

from src.infra.repositories import TaskRepository
from src.services.ingest_service import TaskIngestService
from src.services.review_service import ReviewService
from src.services.stage3_stub import Stage3PipelineStub
from src.ui.review_window import ReviewWindow


_RUNNING_DISPATCH_STATUSES = {"confirmed", "transferring", "running", "uploading"}


class MainWindow(tk.Tk):
    def __init__(
        self,
        task_repository: TaskRepository,
        ingest_service: TaskIngestService,
        review_service: ReviewService,
        stage3_stub: Stage3PipelineStub,
        dispatch_service=None,   # DispatchService | None
        socket_server=None,      # SocketServer | None
    ) -> None:
        super().__init__()
        self.title("Segment By Motion - MVP")
        self.geometry("1360x900")
        self.minsize(1200, 1300)

        self.task_repository = task_repository
        self.ingest_service = ingest_service
        self.review_service = review_service
        self.stage3_stub = stage3_stub
        self.dispatch_service = dispatch_service
        self.socket_server = socket_server

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
        self.task_status_filter_var = tk.StringVar(value="全部")
        self.task_status_filter_combo: ttk.Combobox | None = None

        # 节点状态面板组件
        self.nodes_tree: Optional[ttk.Treeview] = None
        self.dispatch_records_tree: Optional[ttk.Treeview] = None
        self._node_refresh_after_id: Optional[str] = None

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
        self._schedule_node_refresh()
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
        ttk.Label(top, text="状态筛选").grid(row=2, column=7, padx=(10, 4), sticky="e")
        self.task_status_filter_combo = ttk.Combobox(
            top,
            textvariable=self.task_status_filter_var,
            values=["全部"],
            state="readonly",
            width=16,
        )
        self.task_status_filter_combo.grid(row=2, column=8, padx=4, sticky="w")
        self.task_status_filter_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_tasks())
        ttk.Button(top, text="删除所选任务", command=self.delete_selected_task).grid(row=2, column=4, padx=4)
        ttk.Button(top, text="发送到第三阶段(Stub)", command=self.send_to_stage3).grid(row=2, column=5, padx=4)
        ttk.Button(top, text="下发到节点", command=self.dispatch_to_node).grid(row=2, column=6, padx=4)
        ttk.Label(top, text="提示: 双击任务进入 Review").grid(row=3, column=1, columnspan=5, sticky="w", padx=6, pady=(2, 0))

        top.columnconfigure(1, weight=1)

        table_wrap = ttk.Frame(self.task_page)
        table_wrap.pack(fill="both", expand=True, padx=12, pady=(4, 4))

        self.tasks_tree = ttk.Treeview(
            table_wrap,
            columns=("id", "video", "speaker", "status", "segments"),
            show="headings",
            height=10,
        )

        for col, width in (("id", 60), ("video", 420), ("speaker", 120), ("status", 150), ("segments", 100)):
            self.tasks_tree.heading(col, text=col)
            self.tasks_tree.column(col, width=width, anchor="center")
        self.tasks_tree.column("video", anchor="w")

        self.tasks_tree.pack(side="left", fill="both", expand=True)
        self.tasks_tree.bind("<Double-1>", self.open_review_for_selected_task)
        self.tasks_tree.bind("<<TreeviewSelect>>", self._on_task_selected)
        scrollbar = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tasks_tree.yview)
        self.tasks_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        # ── 节点状态面板 ──────────────────────────────────────────────────────
        node_frame = ttk.LabelFrame(self.task_page, text="节点状态")
        node_frame.pack(fill="x", padx=12, pady=(4, 4))

        top_btns = ttk.Frame(node_frame)
        top_btns.pack(fill="x", padx=6, pady=4)
        ttk.Button(top_btns, text="刷新节点", command=self._refresh_nodes).pack(side="left", padx=4)
        ttk.Label(top_btns, text="（每 5 秒自动刷新）").pack(side="left")

        node_tree_wrap = ttk.Frame(node_frame)
        node_tree_wrap.pack(fill="x", padx=6, pady=(0, 4))

        self.nodes_tree = ttk.Treeview(
            node_tree_wrap,
            columns=("node_id", "ip", "status", "dispatch_id", "last_seen"),
            show="headings",
            height=4,
        )
        for col, text, width in (
            ("node_id", "节点ID", 180),
            ("ip", "IP地址", 120),
            ("status", "状态", 80),
            ("dispatch_id", "当前分发", 80),
            ("last_seen", "最近心跳", 200),
        ):
            self.nodes_tree.heading(col, text=text)
            self.nodes_tree.column(col, width=width, anchor="center")
        self.nodes_tree.pack(side="left", fill="x", expand=True)

        # ── 分发进度子面板 ────────────────────────────────────────────────────
        dr_frame = ttk.LabelFrame(self.task_page, text="所选任务分发记录")
        dr_frame.pack(fill="x", padx=12, pady=(0, 8))

        dr_wrap = ttk.Frame(dr_frame)
        dr_wrap.pack(fill="x", padx=6, pady=4)

        self.dispatch_records_tree = ttk.Treeview(
            dr_wrap,
            columns=("id", "node_id", "status", "created_at", "updated_at", "error"),
            show="headings",
            height=4,
        )
        for col, text, width in (
            ("id", "记录ID", 60),
            ("node_id", "节点", 160),
            ("status", "分发状态", 120),
            ("created_at", "创建时间", 180),
            ("updated_at", "更新时间", 180),
            ("error", "错误原因", 200),
        ):
            self.dispatch_records_tree.heading(col, text=text)
            self.dispatch_records_tree.column(col, width=width, anchor="center")
        self.dispatch_records_tree.pack(side="left", fill="x", expand=True)
        dr_sb = ttk.Scrollbar(dr_wrap, orient="vertical", command=self.dispatch_records_tree.yview)
        self.dispatch_records_tree.configure(yscrollcommand=dr_sb.set)
        dr_sb.pack(side="right", fill="y")

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

        rows: list[tuple[int, str, str, str, int]] = []
        display_statuses: set[str] = set()
        for task in self.task_repository.list_tasks():
            dispatch_status: str | None = None
            if self.dispatch_service is not None:
                records = self.dispatch_service.list_dispatch_records(task.id)
                if records:
                    dispatch_status = records[0].dispatch_status

            display_status = self._resolve_display_status(task.status, dispatch_status)
            display_statuses.add(display_status)
            segment_count = self.task_repository.count_segments(task.id)
            rows.append((task.id, task.video_name, task.speaker_id, display_status, segment_count))

        self._refresh_status_filter_options(display_statuses)
        selected_filter = self.task_status_filter_var.get().strip() or "全部"
        for task_id, video_name, speaker_id, display_status, segment_count in rows:
            if not self._status_filter_match(display_status, selected_filter):
                continue
            self.tasks_tree.insert(
                "",
                "end",
                values=(task_id, video_name, speaker_id, display_status, segment_count),
            )

    @staticmethod
    def _resolve_display_status(task_status: str, dispatch_status: str | None) -> str:
        if dispatch_status == "done":
            return "completed"
        if dispatch_status in _RUNNING_DISPATCH_STATUSES:
            return "running"
        if dispatch_status in {"failed", "canceled"}:
            return dispatch_status
        return task_status

    @staticmethod
    def _status_filter_match(display_status: str, selected_filter: str) -> bool:
        if selected_filter in ("", "全部"):
            return True
        return display_status == selected_filter

    def _refresh_status_filter_options(self, display_statuses: set[str]) -> None:
        if self.task_status_filter_combo is None:
            return
        preserved = self.task_status_filter_var.get().strip() or "全部"
        options = ["全部"] + self._ordered_statuses(display_statuses)
        self.task_status_filter_combo["values"] = options
        if preserved in options:
            self.task_status_filter_var.set(preserved)
        else:
            self.task_status_filter_var.set("全部")

    @staticmethod
    def _ordered_statuses(statuses: set[str]) -> list[str]:
        preferred = ["running", "completed"]
        ordered = [s for s in preferred if s in statuses]
        ordered.extend(sorted(s for s in statuses if s not in preferred))
        return ordered

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

    def dispatch_to_node(self) -> None:
        """将 review_done 任务下发到在线节点。"""
        if self.dispatch_service is None or self.socket_server is None:
            messagebox.showerror("功能未启用", "Socket 服务端未初始化")
            return

        task_id = self.selected_task_id()
        if task_id is None:
            messagebox.showinfo("提示", "请先在列表中选择任务")
            return

        # 验证任务状态
        try:
            task = self.task_repository.get_task(task_id)
        except ValueError:
            messagebox.showerror("任务不存在", f"任务 {task_id} 不存在")
            return

        if task.status != "review_done":
            messagebox.showwarning(
                "状态不符",
                f"任务当前状态为 {task.status!r}，仅 review_done 状态可下发到节点",
            )
            return

        # 获取在线节点列表
        online_nodes = self.dispatch_service.list_online_nodes()
        if not online_nodes:
            messagebox.showwarning("无可用节点", "当前没有在线节点，请先连接 Android 处理节点")
            return

        # 节点选择（一个直接用，多个弹对话框）
        if len(online_nodes) == 1:
            node_id = online_nodes[0]["node_id"]
        else:
            node_ids = [n["node_id"] for n in online_nodes]
            node_id = simpledialog.askstring(
                "选择节点",
                f"当前在线节点：\n" + "\n".join(
                    f"  {n['node_id']} ({n['ip']}, {n['status']})"
                    for n in online_nodes
                ) + "\n\n请输入节点ID：",
                initialvalue=node_ids[0],
            )
            if not node_id:
                return
            if node_id not in node_ids:
                messagebox.showerror("节点不存在", f"节点 {node_id!r} 不在线")
                return

        confirmed = messagebox.askyesno(
            "确认下发",
            f"将任务 {task_id}（{task.video_name}）\n下发到节点 {node_id}？",
        )
        if not confirmed:
            return

        # 通过 socket_server 事件循环异步执行分发
        try:
            future = self.socket_server.schedule_coroutine(
                self.dispatch_service.dispatch_task(task_id, node_id)
            )
            messagebox.showinfo(
                "已提交",
                f"任务 {task_id} 已提交下发到节点 {node_id}\n\n"
                "分发进度请在下方「所选任务分发记录」面板中查看。",
            )
            self._refresh_dispatch_records()
        except Exception as exc:
            messagebox.showerror("下发失败", str(exc))

    def _on_task_selected(self, _event=None) -> None:
        """任务列表选中变化时，刷新分发记录面板。"""
        self._refresh_dispatch_records()

    def _schedule_node_refresh(self) -> None:
        """每 5 秒自动刷新节点状态。"""
        self._refresh_nodes()
        self._node_refresh_after_id = self.after(5000, self._schedule_node_refresh)

    def _refresh_nodes(self) -> None:
        """刷新节点状态 Treeview。"""
        if self.nodes_tree is None or self.dispatch_service is None:
            return
        self.nodes_tree.delete(*self.nodes_tree.get_children())
        for node in self.dispatch_service.list_online_nodes():
            self.nodes_tree.insert(
                "",
                "end",
                values=(
                    node.get("node_id", ""),
                    node.get("ip", ""),
                    node.get("status", ""),
                    node.get("dispatch_id") or "",
                    node.get("last_seen", "")[:19],
                ),
            )
        self._refresh_dispatch_records()

    def _refresh_dispatch_records(self) -> None:
        """刷新所选任务的分发记录 Treeview。"""
        if self.dispatch_records_tree is None or self.dispatch_service is None:
            return
        task_id = self.selected_task_id()
        self.dispatch_records_tree.delete(*self.dispatch_records_tree.get_children())
        if task_id is None:
            return
        for rec in self.dispatch_service.list_dispatch_records(task_id):
            self.dispatch_records_tree.insert(
                "",
                "end",
                values=(
                    rec.id,
                    rec.node_id,
                    rec.dispatch_status,
                    rec.created_at.isoformat()[:19],
                    rec.updated_at.isoformat()[:19],
                    rec.error_reason or "",
                ),
            )

    def on_close(self) -> None:
        if self._node_refresh_after_id is not None:
            self.after_cancel(self._node_refresh_after_id)
            self._node_refresh_after_id = None
        if self._create_task_poll_after_id is not None:
            self.after_cancel(self._create_task_poll_after_id)
            self._create_task_poll_after_id = None
        if self._batch_import_poll_after_id is not None:
            self.after_cancel(self._batch_import_poll_after_id)
            self._batch_import_poll_after_id = None
        self._hide_loading()
        self.destroy()

