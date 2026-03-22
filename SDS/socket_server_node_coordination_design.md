# Socket 服务端与处理节点协同设计

## 1. 背景与目标
- 本软件作为 socket 服务端，负责任务编排、状态跟踪、资料分发和结果回收。
- Android 或其它设备作为视频处理节点，负责执行下发任务并回传处理结果。
- 目标：支持节点上线同步、任务状态查询、任务确认后资料传输、断点续传、结果回传与落盘。

## 2. 角色与职责
- 服务端
  - 维护任务队列与状态机。
  - 维护节点注册表（在线状态、能力、当前任务）。
  - 管理传输会话（分片、校验、断点续传）。
- 节点
  - 上报节点信息与本地任务状态。
  - 接收任务并确认。
  - 下载任务资料并执行处理。
  - 上传结果与处理日志。

## 3. 传输通道与端口规划

### 3.1 双通道原则
- 指令传输与数据传输必须使用不同端口。
- 指令通道保持轻量、低延迟，不承载大文件流。
- 数据通道仅用于分片数据与传输确认，避免阻塞控制消息。

### 3.2 端口分配（默认）
- 控制/指令通道端口：`23010`
- 数据传输通道端口：`23011`
- 预留扩展端口段：`23012-23019`（后续用于日志流、监控流、批量回传专用通道）

### 3.3 通道消息映射
- 控制通道（`23010`）：`HELLO`、`HELLO_ACK`、`TASK_ASSIGN`、`TASK_CONFIRM`、`TASK_STATUS_QUERY`、`TASK_STATUS_REPORT`。
- 数据通道（`23011`）：`CHUNK`、`CHUNK_ACK`、`TRANSFER_RESUME_REQUEST`、`TRANSFER_COMPLETE`、结果文件上传。

### 3.4 端口管理要求
- 避免使用 `0-1023` 系统保留端口。
- 建议固定使用上述端口，避免使用动态临时端口段 `49152-65535`。
- 服务端启动时需进行端口占用检测；若冲突，按配置回退到预留端口段并写入启动日志。
- 节点连接时必须显式声明双端口连接结果，任一通道失败均不得进入任务下发阶段。

## 4. 任务状态机
- `created`: 任务已创建。
- `assigned`: 已分配给节点，等待确认。
- `confirmed`: 节点已确认，允许传输资料。
- `transferring`: 资料传输中。
- `running`: 节点处理中。
- `uploading`: 结果回传中。
- `done`: 服务端验收完成。
- `failed`: 执行或传输失败。
- `canceled`: 手动取消。

## 5. 节点上线与状态同步

### 5.1 节点上线（HELLO）
节点发送：
```json
{
  "type": "HELLO",
  "node_id": "android-001",
  "node_version": "1.0.0",
  "capabilities": {"gpu": false, "codec": ["h264", "hevc"]},
  "current_task": {"task_id": 123, "status": "running", "progress": 0.42}
}
```

服务端返回：
```json
{
  "type": "HELLO_ACK",
  "server_time": "2026-03-22T12:00:00Z",
  "sync_actions": [
    {"action": "RESUME_UPLOAD", "task_id": 123},
    {"action": "QUERY_PROGRESS", "task_id": 123}
  ]
}
```

### 5.2 状态查询
- 服务端可主动发送 `TASK_STATUS_QUERY`。
- 节点应返回 `TASK_STATUS_REPORT`，包含进度、阶段、最近错误。

## 6. 任务下发与确认

### 6.1 下发任务元数据
服务端发送 `TASK_ASSIGN`：
- `task_id`
- `video_meta`（名称、大小、校验）
- `processing_params`
- `result_requirements`

### 6.2 节点确认
- 节点返回 `TASK_CONFIRM`（接受/拒绝 + 原因）。
- 服务端收到确认后将任务状态置为 `confirmed`，进入资料传输。

## 7. 资料传输与断点续传

### 7.1 分片协议
- 使用固定大小 chunk（如 1MB）。
- 每片携带：`task_id`、`transfer_id`、`chunk_index`、`chunk_hash`、`payload`。
- 每片确认：`CHUNK_ACK`。

### 7.2 断点续传
- 节点断开后重连，发送 `TRANSFER_RESUME_REQUEST`（包含已接收 chunk bitmap 或最后确认索引）。
- 服务端按缺失分片补发。
- 全量完成后，节点发送 `TRANSFER_COMPLETE`，服务端校验总 hash。

## 8. 结果回传与存储

### 8.1 回传内容
- 必选：结果清单（JSON）、处理日志、热度数据导出（JSON+CSV）。
- 可选：中间产物、调试快照。

### 8.2 服务端落盘建议
- `data/node_results/<task_id>/<node_id>/`
  - `result.json`
  - `heat_export.json`
  - `heat_export.csv`
  - `logs/*.log`
- 完成后更新任务状态 `done`，记录 `completed_at` 与摘要信息。

## 9. 一致性与安全
- 消息级别 `request_id` 防重。
- 传输分片 hash + 文件总 hash 双重校验。
- 建议支持 token 或 mTLS 认证。
- 关键状态变更写入审计日志。

## 10. 失败恢复策略
- 网络中断：基于 `transfer_id` 断点续传。
- 节点崩溃：上线后通过 `HELLO` 回传当前任务状态并恢复。
- 服务端重启：从数据库恢复任务状态和未完成传输会话。

## 11. 与当前版本衔接
- 当前 GUI 版本可先保留本地处理流程。
- socket 协同先按本文档完成接口约定与状态机字段预留。
- 后续可在 `services` 层新增 `dispatch_service` 与 `transfer_service` 实现在线节点调度。

