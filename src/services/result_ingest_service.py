"""
ResultIngestService — 结果分片组装、SHA-256 校验、result.json 验证与落盘。

遵循 SDS/export_data_design.md §3 的 JSON 结构约定（task/summary/segments/label_events）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class ResultIngestService:
    """组装 upload 分片 → 校验 → 写入最终结果目录。"""

    def ingest(
        self,
        task_id: int,
        node_id: str,
        dispatch_record_id: int,
        chunks_base: Path,
        out_dir: Path,
        total_hash: str,
        source_video_name: str | None = None,
    ) -> None:
        """
        组装各 fileRole 分片文件，校验视频 hash，写入最终目录。

        :param task_id:             Python 域任务 ID
        :param node_id:             节点 ID
        :param dispatch_record_id:  分发记录 ID（用于审计）
        :param chunks_base:         chunks 根目录 .../chunks/<transferId>/
        :param out_dir:             最终落盘目录 .../node_results/<taskId>/<nodeId>/
        :param total_hash:          ResultTransferComplete.totalHash（视频文件 SHA-256）
        :param source_video_name:   原始视频文件名（用于生成 *_cut 命名）
        :raises ValueError:         hash 不匹配或必要文件缺失
        """
        out_dir.mkdir(parents=True, exist_ok=True)

        if not chunks_base.exists():
            raise ValueError(
                f"task={task_id}: 找不到 chunks 目录: {chunks_base}"
            )

        # 组装各 fileRole 分片
        assembled: dict[str, Path] = {}
        for role_dir in sorted(chunks_base.iterdir()):
            if not role_dir.is_dir():
                continue
            file_role = role_dir.name
            out_path = self._assemble_chunks(
                role_dir, out_dir, file_role, source_video_name
            )
            assembled[file_role] = out_path
            logger.info(
                "结果文件组装完成: task=%d role=%s size=%d B",
                task_id,
                file_role,
                out_path.stat().st_size,
            )

        # 校验视频文件 hash（totalHash 是视频文件的 SHA-256）
        video_path = assembled.get("video")
        if video_path is None or not video_path.exists():
            raise ValueError(f"task={task_id}: 未找到 video 角色结果文件")

        actual_hash = _sha256_file(video_path)
        if actual_hash != total_hash:
            raise ValueError(
                f"task={task_id}: 视频文件 hash 不匹配 "
                f"expected={total_hash[:16]}... actual={actual_hash[:16]}..."
            )

        # 解析并基础验证 result.json（如存在，非致命）
        json_path = assembled.get("json")
        if json_path and json_path.exists():
            self._validate_result_json(json_path, task_id)

        logger.info(
            "task=%d 结果验收通过，落盘于 %s (node=%s)",
            task_id,
            out_dir,
            node_id,
        )
        # 结果已验收完成，清理回传分片临时目录，避免长期占用磁盘。
        shutil.rmtree(chunks_base, ignore_errors=True)

    # ── 内部 ──────────────────────────────────────────────────────────────────

    def _assemble_chunks(
        self,
        role_dir: Path,
        out_dir: Path,
        file_role: str,
        source_video_name: str | None,
    ) -> Path:
        """按 chunk_index 升序拼接所有 .bin 分片，返回输出文件路径。"""
        ext_map = {"video": ".mp4", "json": ".json", "log": ".log"}
        if file_role == "video":
            out_path = out_dir / _build_cut_video_name(source_video_name)
        else:
            ext = ext_map.get(file_role, f".{file_role}")
            out_path = out_dir / f"result{ext}"

        chunk_files = sorted(
            role_dir.glob("*.bin"),
            key=lambda p: int(p.stem),
        )
        if not chunk_files:
            raise ValueError(
                f"fileRole={file_role} 没有任何分片文件（dir={role_dir}）"
            )

        with open(out_path, "wb") as out_f:
            for chunk_file in chunk_files:
                out_f.write(chunk_file.read_bytes())

        return out_path

    def _validate_result_json(self, json_path: Path, task_id: int) -> None:
        """基础验证 result.json 结构（非致命，仅 warning）。"""
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("task=%d result.json 解析失败（非致命）: %s", task_id, exc)
            return

        required_keys = {"task", "summary", "segments", "label_events"}
        missing = required_keys - set(data.keys())
        if missing:
            logger.warning(
                "task=%d result.json 缺少字段 %s（非致命）", task_id, missing
            )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(8 * 1024 * 1024)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _build_cut_video_name(source_video_name: str | None) -> str:
    if not source_video_name:
        return "result_cut.mp4"
    p = Path(source_video_name)
    ext = p.suffix or ".mp4"
    return f"{p.stem}_cut{ext}"


