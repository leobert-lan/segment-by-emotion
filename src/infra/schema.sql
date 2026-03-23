PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_path TEXT NOT NULL,
    video_name TEXT NOT NULL,
    speaker_id TEXT NOT NULL,
    status TEXT NOT NULL,
    segment_duration REAL NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    start_sec REAL NOT NULL,
    end_sec REAL NOT NULL,
    heat_score REAL NOT NULL,
    current_label TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS label_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    segment_id INTEGER NOT NULL,
    previous_label TEXT,
    new_label TEXT NOT NULL,
    undone INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (segment_id) REFERENCES segments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS threshold_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    speaker_id TEXT NOT NULL,
    profile_name TEXT NOT NULL DEFAULT 'default',
    min_threshold REAL NOT NULL,
    max_threshold REAL NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (speaker_id, profile_name)
);

-- ── 节点分发扩展表 ─────────────────────────────────────────────────────────

-- 节点注册表
CREATE TABLE IF NOT EXISTS dispatch_nodes (
    node_id TEXT PRIMARY KEY,
    last_ip TEXT,
    capabilities_json TEXT,
    status TEXT NOT NULL DEFAULT 'offline',  -- 'online' | 'busy' | 'offline'
    current_dispatch_id INTEGER,
    last_seen_at TEXT,
    registered_at TEXT NOT NULL
);

-- 任务分发记录（一次 dispatch 对应一条）
CREATE TABLE IF NOT EXISTS dispatch_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    dispatch_status TEXT NOT NULL DEFAULT 'assigned',
    -- assigned → confirmed → transferring → running → uploading → done | failed | canceled
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- 传输会话（每次文件传输对应一条，upload 每种 fileRole 单独一条）
CREATE TABLE IF NOT EXISTS transfer_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dispatch_record_id INTEGER NOT NULL,
    transfer_id TEXT NOT NULL UNIQUE,
    direction TEXT NOT NULL,             -- 'download' | 'upload'
    file_role TEXT NOT NULL DEFAULT 'video',  -- 'video' | 'json' | 'log'
    total_chunks INTEGER NOT NULL,
    file_hash TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'in_progress',  -- 'in_progress' | 'complete' | 'failed'
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (dispatch_record_id) REFERENCES dispatch_records(id) ON DELETE CASCADE
);

-- 分片确认跟踪（仅用于 download 方向断点续传）
CREATE TABLE IF NOT EXISTS transfer_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_hash TEXT,
    acked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    acked_at TEXT,
    UNIQUE (session_id, chunk_index),
    FOREIGN KEY (session_id) REFERENCES transfer_sessions(id) ON DELETE CASCADE
);

-- 关键操作审计日志
CREATE TABLE IF NOT EXISTS dispatch_audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dispatch_record_id INTEGER,
    node_id TEXT,
    event_type TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL
);

