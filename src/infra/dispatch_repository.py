"""
DispatchRepository — 节点注册、分发记录、传输会话、分片跟踪、审计日志的 DB CRUD。

遵循项目惯例：所有写操作通过 Database.session() context manager。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from src.domain.models import DispatchNode, DispatchRecord, TransferSession
from src.infra.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DispatchRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    # ── 节点注册 ──────────────────────────────────────────────────────────────

    def upsert_node(
        self,
        node_id: str,
        last_ip: str,
        capabilities_json: str,
        status: str,
    ) -> None:
        now = _now()
        with self.database.session() as conn:
            conn.execute(
                """
                INSERT INTO dispatch_nodes
                    (node_id, last_ip, capabilities_json, status, last_seen_at, registered_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    last_ip = excluded.last_ip,
                    capabilities_json = excluded.capabilities_json,
                    status = excluded.status,
                    last_seen_at = excluded.last_seen_at
                """,
                (node_id, last_ip, capabilities_json, status, now, now),
            )

    def update_node_status(
        self,
        node_id: str,
        status: str,
        current_dispatch_id: Optional[int] = None,
    ) -> None:
        now = _now()
        with self.database.session() as conn:
            conn.execute(
                """
                UPDATE dispatch_nodes
                SET status = ?, current_dispatch_id = ?, last_seen_at = ?
                WHERE node_id = ?
                """,
                (status, current_dispatch_id, now, node_id),
            )

    def list_nodes(self) -> list[DispatchNode]:
        with self.database.session() as conn:
            rows = conn.execute(
                "SELECT * FROM dispatch_nodes ORDER BY last_seen_at DESC"
            ).fetchall()
        return [DispatchNode.from_row(r) for r in rows]

    def get_node(self, node_id: str) -> Optional[DispatchNode]:
        with self.database.session() as conn:
            row = conn.execute(
                "SELECT * FROM dispatch_nodes WHERE node_id = ?", (node_id,)
            ).fetchone()
        return DispatchNode.from_row(row) if row else None

    # ── 分发记录 ──────────────────────────────────────────────────────────────

    def create_dispatch_record(self, task_id: int, node_id: str) -> DispatchRecord:
        now = _now()
        with self.database.session() as conn:
            cursor = conn.execute(
                """
                INSERT INTO dispatch_records
                    (task_id, node_id, dispatch_status, created_at, updated_at)
                VALUES (?, ?, 'assigned', ?, ?)
                """,
                (task_id, node_id, now, now),
            )
            record_id = cursor.lastrowid
        return self.get_dispatch_record(record_id)  # type: ignore[arg-type]

    def get_dispatch_record(self, record_id: int) -> Optional[DispatchRecord]:
        with self.database.session() as conn:
            row = conn.execute(
                "SELECT * FROM dispatch_records WHERE id = ?", (record_id,)
            ).fetchone()
        return DispatchRecord.from_row(row) if row else None

    def get_active_record_for_node(self, node_id: str) -> Optional[DispatchRecord]:
        """返回该节点最近一条未完成的分发记录。"""
        with self.database.session() as conn:
            row = conn.execute(
                """
                SELECT * FROM dispatch_records
                WHERE node_id = ?
                  AND dispatch_status NOT IN ('done', 'failed', 'canceled')
                ORDER BY id DESC LIMIT 1
                """,
                (node_id,),
            ).fetchone()
        return DispatchRecord.from_row(row) if row else None

    def list_records_for_task(self, task_id: int) -> list[DispatchRecord]:
        with self.database.session() as conn:
            rows = conn.execute(
                "SELECT * FROM dispatch_records WHERE task_id = ? ORDER BY id DESC",
                (task_id,),
            ).fetchall()
        return [DispatchRecord.from_row(r) for r in rows]

    def update_dispatch_status(
        self,
        record_id: int,
        status: str,
        error_reason: Optional[str] = None,
    ) -> None:
        now = _now()
        completed_at = now if status in ("done", "failed", "canceled") else None
        with self.database.session() as conn:
            conn.execute(
                """
                UPDATE dispatch_records
                SET dispatch_status = ?, error_reason = ?, updated_at = ?,
                    completed_at = COALESCE(?, completed_at)
                WHERE id = ?
                """,
                (status, error_reason, now, completed_at, record_id),
            )

    # ── 传输会话 ──────────────────────────────────────────────────────────────

    def create_transfer_session(
        self,
        dispatch_record_id: int,
        transfer_id: str,
        direction: str,
        file_role: str,
        total_chunks: int,
        file_hash: str,
        file_size_bytes: int,
    ) -> TransferSession:
        now = _now()
        with self.database.session() as conn:
            cursor = conn.execute(
                """
                INSERT INTO transfer_sessions
                    (dispatch_record_id, transfer_id, direction, file_role,
                     total_chunks, file_hash, file_size_bytes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dispatch_record_id,
                    transfer_id,
                    direction,
                    file_role,
                    total_chunks,
                    file_hash,
                    file_size_bytes,
                    now,
                ),
            )
            session_id = cursor.lastrowid

            # 预建分片跟踪行（仅 download）
            if direction == "download":
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO transfer_chunks
                        (session_id, chunk_index, acked, created_at)
                    VALUES (?, ?, 0, ?)
                    """,
                    [(session_id, i, now) for i in range(total_chunks)],
                )

        return self.get_transfer_session_by_id(session_id)  # type: ignore[arg-type]

    def get_transfer_session_by_id(self, session_id: int) -> Optional[TransferSession]:
        with self.database.session() as conn:
            row = conn.execute(
                "SELECT * FROM transfer_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return TransferSession.from_row(row) if row else None

    def get_transfer_session_by_transfer_id(
        self, transfer_id: str
    ) -> Optional[TransferSession]:
        with self.database.session() as conn:
            row = conn.execute(
                "SELECT * FROM transfer_sessions WHERE transfer_id = ?",
                (transfer_id,),
            ).fetchone()
        return TransferSession.from_row(row) if row else None

    def get_transfer_sessions_for_record(
        self, dispatch_record_id: int, direction: Optional[str] = None
    ) -> list[TransferSession]:
        query = "SELECT * FROM transfer_sessions WHERE dispatch_record_id = ?"
        params: list = [dispatch_record_id]
        if direction:
            query += " AND direction = ?"
            params.append(direction)
        with self.database.session() as conn:
            rows = conn.execute(query, params).fetchall()
        return [TransferSession.from_row(r) for r in rows]

    def mark_chunk_acked(self, session_id: int, chunk_index: int) -> None:
        now = _now()
        with self.database.session() as conn:
            conn.execute(
                """
                UPDATE transfer_chunks
                SET acked = 1, acked_at = ?
                WHERE session_id = ? AND chunk_index = ?
                """,
                (now, session_id, chunk_index),
            )

    def get_missing_chunk_indices(self, session_id: int) -> list[int]:
        """返回未被 ACK 的分片索引列表（升序）。"""
        with self.database.session() as conn:
            rows = conn.execute(
                """
                SELECT chunk_index FROM transfer_chunks
                WHERE session_id = ? AND acked = 0
                ORDER BY chunk_index
                """,
                (session_id,),
            ).fetchall()
        return [r["chunk_index"] for r in rows]

    def complete_transfer_session(self, session_id: int) -> None:
        now = _now()
        with self.database.session() as conn:
            conn.execute(
                """
                UPDATE transfer_sessions
                SET status = 'complete', completed_at = ?
                WHERE id = ?
                """,
                (now, session_id),
            )

    def fail_transfer_session(self, session_id: int) -> None:
        with self.database.session() as conn:
            conn.execute(
                "UPDATE transfer_sessions SET status = 'failed' WHERE id = ?",
                (session_id,),
            )

    # ── 审计日志 ──────────────────────────────────────────────────────────────

    def append_audit_log(
        self,
        node_id: str,
        event_type: str,
        description: str,
        dispatch_record_id: Optional[int] = None,
    ) -> None:
        now = _now()
        with self.database.session() as conn:
            conn.execute(
                """
                INSERT INTO dispatch_audit_logs
                    (dispatch_record_id, node_id, event_type, description, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (dispatch_record_id, node_id, event_type, description, now),
            )

