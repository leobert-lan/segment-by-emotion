# Android 节点端任务清单

## 1. 目标
完成 Android 视频处理节点的开发，实现"接收任务 → 下载视频 → 裁剪/合并/压缩 → 回传结果"的完整闭环，与 Python 主程序通过局域网 TCP Socket 协同运作。

## 2. 范围
- 协议实现：遵循 `SDS/socket_server_node_coordination_design.md`
- 媒体处理：利用骁龙 880 硬件编解码，实现 `SDS/export_data_design.md` 定义的输出格式
- 架构：遵循 `SDS/android_node_design.md` 的分层设计

---

## 3. 任务清单

### M0 — 工程基础

- [ ] `libs.versions.toml`：添加 media3、room、ksp、kotlinx-serialization、coroutines、datastore、lifecycle-service 版本条目
- [ ] `app/build.gradle.kts`：添加 KSP 插件、kotlin-serialization 插件；添加所有 `implementation`/`ksp` 依赖项
- [ ] `AndroidManifest.xml`：添加 INTERNET、FOREGROUND_SERVICE、FOREGROUND_SERVICE_DATA_SYNC、WAKE_LOCK 权限；声明 MediaNodeService（foregroundServiceType="dataSync"）
- [ ] 创建全部包目录（domain/model、domain/state、net/protocol、net/socket、media/codec、media/pipeline、storage/db、storage/entity、storage/file、storage/prefs、service、ui/connection、ui/status、ui/navigation）
- [ ] 实现 `domain/model`：NodeTask、VideoMeta、ProcessingParams、VideoSegment、ProcessingResult、TransferProgress 数据类
- [ ] 实现 `domain/state/TaskState` 密封类（含所有状态变体和 stage 枚举）
- [ ] 实现 `storage/prefs/NodePreferences`（DataStore 封装：serverHost、controlPort、dataPort、nodeId、nodeVersion）

### M1 — 协议固化

- [ ] 实现 `net/protocol/ControlMessage`：`@Serializable` 密封类，含 Hello/HelloAck/TaskAssign/TaskConfirm/TaskStatusQuery/TaskStatusReport，每条消息携带 `requestId`
- [ ] 实现 `net/protocol/DataMessage`：`@Serializable` 密封类，含 Chunk/ChunkAck/TransferResumeRequest/TransferComplete
- [ ] 实现 `net/protocol/MessageFramer`：控制通道 newline-JSON 编解码；数据通道 `[4B-len][JSON-header][binary-payload]` 帧格式
- [ ] 单元测试：ControlMessage/DataMessage 全消息类型序列化/反序列化往返
- [ ] 单元测试：MessageFramer 帧编解码（含超长 payload、空 payload 边界情况）
- [ ] 协议评审：与 `socket_server_node_coordination_design.md` 逐字段核对，输出评审确认

### M2 — 基础联通

- [ ] 实现 `net/socket/ControlChannelClient`：TCP 连接 :23010、协程读循环→SharedFlow、Mutex 写保护、连接后自动发送 HELLO、30 秒心跳
- [ ] 实现 `net/socket/DataChannelClient`：TCP 连接 :23011、帧读循环→分片写入+ChunkAck、上传写出路径
- [ ] 实现 `net/socket/SocketConnectionManager`：双通道协调、指数退避重连（1/2/4/8…60s）、ConnectionState 状态流；双通道均就绪后才转为 Ready
- [ ] 实现 `storage/entity/LocalTaskEntity` + `TransferChunkEntity`（含正确主键和外键约束）
- [ ] 实现 `storage/db/TaskDao`：upsert、getByStatus、updateStatus
- [ ] 实现 `storage/db/ChunkDao`：insertOrIgnore、markReceived、getMissingIndices、countReceived
- [ ] 实现 `storage/db/AppDatabase`（Room，版本 1，node_state.db）
- [ ] 实现 `storage/file/FileStoreManager`：per-task 目录创建、writeChunkPayload、assembleFile、resultStagingDir
- [ ] 实现 `service/MediaNodeService` 骨架：LifecycleService、通知渠道、startForeground、ACTION_CONNECT/ACTION_DISCONNECT
- [ ] 实现 `ui/connection/ConnectionScreen` + `ConnectionViewModel`（读写 NodePreferences，触发服务启动）
- [ ] 实现 `ui/navigation/AppNavHost`（connection ↔ status 路由）
- [ ] 更新 `MainActivity` 以承载导航宿主
- [ ] 集成测试：连接本地 mock 服务器，完成 HELLO/HELLO_ACK 交换，双通道同时就绪验证

### M3 — 可恢复传输

- [ ] 实现启动时恢复流程：查询 `status NOT IN ('done','idle')` 任务 → `getMissingIndices` → 发送 TRANSFER_RESUME_REQUEST
- [ ] 实现 `TaskOrchestrator`：完整状态机协程，覆盖 Connecting→Receiving→Processing→Uploading→Done/Error 全路径
- [ ] 实现 HELLO_ACK `sync_actions` 处理（RESUME_UPLOAD / QUERY_PROGRESS）
- [ ] 实现 `request_id` 幂等缓存（最近 64 条处理记录）
- [ ] 集成测试：模拟传输中途断线 → 重连 → 验证缺失分片续传 → 文件完整性校验通过
- [ ] 集成测试：节点重启后恢复到上次任务状态 → 自动发送 TRANSFER_RESUME_REQUEST

### M4 — 媒体处理流水线

- [ ] 实现 `media/codec/HardwareCodecSelector`：遍历 MediaCodecList，优先选取 Qualcomm 硬件编码器（`c2.qti.hevc.encoder` / `c2.qti.avc.encoder`），记录回退日志
- [ ] 实现 `media/pipeline/SegmentCutter`：MediaExtractor + MediaMuxer pass-through 裁剪；非 IDR 起点时回退 Media3 Transformer ClippingConfiguration
- [ ] 实现 `media/pipeline/SegmentMerger`：MediaMuxer 多片段单调 PTS 拼接
- [ ] 实现 `media/pipeline/VideoCompressor`：Media3 Transformer + HW 编码器配置（targetBitrate、可选降分辨率）
- [ ] 实现 `media/pipeline/MediaPipeline`：suspend fun execute，阶段化进度回调，输出 result.mp4 + result.json
- [ ] `result.json` 字段验证：与 `SDS/export_data_design.md` §3 逐字段对齐（task/summary/segments/label_events）
- [ ] 单元测试：合成短视频（1s 正弦波 H.264）输入 → 流水线输出文件存在、可解析、时长正确
- [ ] 仪器测试（设备）：骁龙 880 上确认编码器名称为 `c2.qti.hevc.encoder`，处理速度 ≥ 实时速度

### M5 — 结果回传与全链路联调

- [ ] 实现 `service/UploadManager`：读取 result.mp4 + result.json，1 MB 分片，逐片发送+等待 ChunkAck，最终发送 TransferComplete
- [ ] 实现 `service/MediaNodeService` 完整版：通知进度随 TaskState 更新，包含确定进度条和不确定进度条切换
- [ ] 实现 `ui/status/NodeStatusScreen` + `NodeStatusViewModel`：Task 信息卡片、阶段进度、错误横幅、断开按钮
- [ ] 端到端测试（真实设备 + Python 服务器）：TASK_ASSIGN → 接收视频 → 处理 → 上传 → 服务端状态置 `done`、结果落盘验收通过
- [ ] 压测：连续处理 5 个任务，观察内存/温度/电量消耗，处理耗时记录

---

## 4. 里程碑

| 里程碑 | 目标 | 验收条件 |
|--------|------|---------|
| M0 基础建设 | 工程可编译，依赖解析成功 | `./gradlew assembleDebug` 无报错 |
| M1 协议固化 | 消息序列化往返无损 | 所有协议单元测试通过 |
| M2 基础联通 | 节点可与服务端完成握手 | HELLO/HELLO_ACK 集成测试通过 |
| M3 可恢复传输 | 断线续传可验证 | 续传集成测试 + 重启恢复测试通过 |
| M4 媒体流水线 | 硬件加速处理可用 | 骁龙 880 设备仪器测试通过 |
| M5 全链路闭环 | GUI→节点处理→回传落盘 | 端到端测试通过，服务端状态 done |

---

## 5. 验收标准

- 节点异常断开后 30 秒内可恢复到可继续传输状态（与 SRS 整体验收标准一致）
- 处理后的视频文件可正常播放，时间区间与 `interesting` 片段吻合
- 回传的 `result.json` 可被 Python 服务端无修改解析和验收
- 骁龙 880 设备上，1080p 10 分钟视频的裁剪+合并+压缩耗时 ≤ 5 分钟
- 应用在后台处理时 ForegroundService 通知正常显示，不被系统杀死

