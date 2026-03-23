# Android 视频处理节点设计文档

## 1. 概述

Android 端作为视频裁剪/合并/压缩的处理节点，通过局域网 TCP Socket 与 Python 主程序通信。
利用高通骁龙 880 芯片的硬件编解码能力（Adreno 660 GPU、Hexagon 780 DSP、c2.qti.hevc/avc 编码器）实现高性能视频处理。

- 目标工程：`MediaService/`，包名 `osp.leobert.androd.mediaservice`
- minSdk = 31，Kotlin + Jetpack Compose，AGP 9.x
- 通信协议：遵循 `SDS/socket_server_node_coordination_design.md`
- 输出格式：遵循 `SDS/export_data_design.md`

---

## 2. 分层架构

```
osp.leobert.androd.mediaservice/
├── domain/
│   ├── model/           ← 纯 Kotlin 数据类（无 Android 框架引用）
│   └── state/           ← TaskState 密封类（节点端状态机）
├── net/
│   ├── protocol/        ← @Serializable 密封消息类（控制+数据通道）
│   └── socket/          ← ControlChannelClient、DataChannelClient、SocketConnectionManager
├── media/
│   ├── codec/           ← HardwareCodecSelector（MediaCodecList 探测）
│   └── pipeline/        ← SegmentCutter、SegmentMerger、VideoCompressor、MediaPipeline
├── storage/
│   ├── db/              ← Room AppDatabase、TaskDao、ChunkDao
│   ├── entity/          ← LocalTaskEntity、TransferChunkEntity
│   ├── file/            ← FileStoreManager（分片写入、组装）
│   └── prefs/           ← NodePreferences（DataStore：server host/port/nodeId）
├── service/
│   └── MediaNodeService ← ForegroundService；TaskOrchestrator；UploadManager
├── ui/
│   ├── connection/      ← ConnectionScreen + ConnectionViewModel
│   ├── status/          ← NodeStatusScreen + NodeStatusViewModel
│   └── navigation/      ← AppNavHost
└── MainActivity.kt
```

**层间依赖规则（与 Python 端一致）：**
- `domain` ← `net/protocol`、`storage` ← `media` ← `service` ← `ui`
- UI 不直接调用 media 或 db；只通过 ViewModel → Service 交互。

---

## 3. 领域模型（domain/model）

| 类 | 字段 | 说明 |
|----|------|------|
| `NodeTask` | taskId, videoMeta, processingParams, localStatus | 对应 TASK_ASSIGN 元数据 + 本地处理字段 |
| `VideoMeta` | videoName, fileSizeBytes, totalChunks, fileHash | 视频文件传输元信息 |
| `ProcessingParams` | segments: List<VideoSegment>, codecHint, targetBitrateKbps | 服务端指定的裁剪区间和压缩参数 |
| `VideoSegment` | startMs, endMs | 单个 interesting 片段时间区间（毫秒） |
| `ProcessingResult` | taskId, outputFilePath, outputFileSizeBytes, summaryJson | 处理结果，映射 export_data_design.md §3 |
| `TransferProgress` | taskId, totalChunks, receivedBitSet, lastConfirmedIndex | 断点续传状态 |

---

## 4. 节点状态机（domain/state/TaskState）

```
Idle
  │ [用户点击连接]
  ▼
Connecting(host, controlPort, dataPort)
  │ HELLO_ACK 收到 + 双通道就绪
  ▼
[等待 TASK_ASSIGN]
  │ TASK_ASSIGN 收到
  ▼
Receiving(taskId, videoName, progress 0→1)
  │ TRANSFER_COMPLETE + 文件组装完成
  ▼
Processing(taskId, stage: CUT|MERGE|COMPRESS, progress 0→1)
  │ MediaPipeline 完成
  ▼
Uploading(taskId, progress 0→1)
  │ 服务端 TRANSFER_COMPLETE 确认
  ▼
Done(taskId)
  │ [用户重置]
  ▼
Idle

Error(taskId?, reason, recoverable: Boolean)
  ├─ recoverable=true → [自动重连] → Connecting
  └─ recoverable=false → [用户手动重置] → Idle
```

`TaskOrchestrator` 持有唯一 `MutableStateFlow<TaskState>`，状态转换只在 orchestrator 协程内进行（单写者）。

---

## 5. 通信协议（net/protocol）

### 5.1 控制通道消息（端口 23010，newline-delimited JSON）

```kotlin
// 每条消息包含 request_id 用于幂等
@Serializable sealed class ControlMessage {
    data class Hello(val nodeId, val nodeVersion, val capabilities, val currentTask?) : ControlMessage()
    data class HelloAck(val serverTime, val syncActions: List<SyncAction>) : ControlMessage()
    data class TaskAssign(val taskId, val videoMeta, val processingParams, val resultRequirements) : ControlMessage()
    data class TaskConfirm(val taskId, val accepted: Boolean, val reason?) : ControlMessage()
    data class TaskStatusQuery(val taskId) : ControlMessage()
    data class TaskStatusReport(val taskId, val status, val progress, val stage?, val lastError?) : ControlMessage()
}
```

### 5.2 数据通道消息（端口 23011，[4B-len][JSON-header][binary-payload] 帧格式）

```kotlin
@Serializable sealed class DataMessage {
    data class Chunk(val taskId, val transferId, val chunkIndex, val chunkHash, val payloadSize) : DataMessage()
    data class ChunkAck(val taskId, val transferId, val chunkIndex) : DataMessage()
    data class TransferResumeRequest(val taskId, val transferId, val missingIndices: List<Int>) : DataMessage()
    data class TransferComplete(val taskId, val transferId, val totalHash) : DataMessage()
}
```

### 5.3 MessageFramer

- **控制通道**：`\n` 分隔的 JSON 字符串，`BufferedReader.readLine()` 读取
- **数据通道**：`[4字节 Big-Endian header-JSON长度][header JSON][binary payload]`
  - 写入：`DataOutputStream.writeInt(len) + write(headerBytes) + write(payload)`
  - 读取：`DataInputStream.readInt()` → 读 header → 读 payload

---

## 6. Socket 通道客户端（net/socket）

### ControlChannelClient
- TCP 连接 `host:23010`；`Dispatchers.IO` 协程读循环，解析 `ControlMessage`，发送至 `SharedFlow`
- `suspend send(msg: ControlMessage)`：Mutex 保护，序列化为 JSON + `\n`
- 连接成功后立即发送 `HELLO`（携带能力信息和当前任务状态）
- 每 30 秒发送一次 `TaskStatusReport`（有活跃任务时）

### DataChannelClient
- TCP 连接 `host:23011`；帧格式读循环，调用 `ChunkDao.markReceived + FileStoreManager.writeChunk`
- 每收到一个 chunk 后发送 `ChunkAck`
- 上传路径：`UploadManager` 通过同一 `DataChannelClient` 写出结果分片

### SocketConnectionManager
- 同时建立两条通道；只有两者均就绪后才转为 `ConnectionState.Ready`（SDS §3.4）
- 任一通道断开 → `ConnectionState.Failed` → 指数退避重连（1s, 2s, 4s, 8s…, max 60s）

---

## 7. 视频处理流水线（media/）

### 7.1 硬件编解码器选择（codec/HardwareCodecSelector）

```kotlin
// 优先级：HEVC HW > AVC HW > SW fallback
fun selectEncoder(mimeType: String): String {
    return MediaCodecList(REGULAR_CODECS).codecInfos
        .filter { it.isEncoder && it.supportedTypes.contains(mimeType) }
        .firstOrNull { !it.name.startsWith("OMX.google") && !it.name.startsWith("c2.android") }
        ?.name
        ?: MediaCodecList.findEncoderForFormat(MediaFormat.createVideoFormat(mimeType, 1920, 1080))
}
// 骁龙 880 期望结果：c2.qti.hevc.encoder / c2.qti.avc.encoder
```

### 7.2 处理流水线

```
输入视频（接收完成的 .mp4）
    │
    ▼ SegmentCutter  → MediaExtractor + MediaMuxer，pass-through 模式（无转码）
    │  按 VideoSegment 列表裁剪出若干 temp_seg_N.mp4
    │  说明：pass-through 最快；非 IDR 起点问题由 Media3 ClippingConfiguration 兜底
    │
    ▼ SegmentMerger  → MediaMuxer，单调 PTS 拼接
    │  → merged.mp4
    │
    ▼ VideoCompressor → Media3 Transformer
       编码器：HardwareCodecSelector → c2.qti.hevc.encoder（HEVC，Hexagon 780 DSP）
       配置：targetBitrate 2 Mbps @ 1080p；可选降分辨率（Effects）
       回退：c2.qti.avc.encoder → OMX.google.h264.encoder
    │
    └─▶ result_<taskId>.mp4  +  result.json（符合 export_data_design.md §3 契约）
```

### 7.3 进度上报

`MediaPipeline.execute()` 是 `suspend fun`，通过 `StateFlow` 上报 `Processing(stage, 0→1)` 进度更新给 `TaskOrchestrator`。

---

## 8. 本地持久化（storage/）

### Room 表结构

**`local_tasks` 表**
| 列 | 类型 | 说明 |
|----|------|------|
| taskId | TEXT PK | 服务端 task_id（字符串化） |
| videoName | TEXT | 视频文件名 |
| fileSizeBytes | INTEGER | 文件总字节数 |
| totalChunks | INTEGER | 总 chunk 数 |
| fileHash | TEXT | 文件 SHA-256 |
| processingParamsJson | TEXT | ProcessingParams JSON |
| status | TEXT | 对应 TaskState 名称 |
| errorMessage | TEXT? | 错误信息 |
| createdAt | TEXT | ISO-8601 |
| updatedAt | TEXT | ISO-8601 |

**`transfer_chunks` 表**
| 列 | 类型 | 说明 |
|----|------|------|
| taskId | TEXT | FK → local_tasks |
| chunkIndex | INTEGER | 片序号 |
| chunkHash | TEXT | 分片 SHA-256 |
| received | INTEGER | 0/1 |
| fileOffset | INTEGER | 写入目标文件偏移量 |
| PRIMARY KEY (taskId, chunkIndex) | | |

### FileStoreManager
- 每个任务目录：`<filesDir>/tasks/<taskId>/`
  - `chunks/chunk_<index>.bin`：接收缓冲
  - `assembled.mp4`：分片组装后的输入文件
  - `output/result_<taskId>.mp4`：处理结果
  - `result.json`：结果摘要
  - `processing.log`：处理日志

### NodePreferences（DataStore）
- `serverHost`, `controlPort`(23010), `dataPort`(23011), `nodeId`(UUID), `nodeVersion`

---

## 9. ForegroundService（service/MediaNodeService）

- 继承 `LifecycleService`（lifecycle-service 依赖）
- `onCreate`：创建通知渠道 `"node_processing"`（importance = LOW）
- `onStartCommand(ACTION_CONNECT)`：`startForeground(1, buildNotification(Connecting))`；在 `lifecycleScope` 启动 `TaskOrchestrator.run()`
- 通知内容随 `TaskState` 更新：进度条（Receiving/Uploading 确定进度），转圈（Processing），静态文字（Done/Error）
- 通知 Action：**停止**（发送 `ACTION_DISCONNECT` Intent）
- `onStartCommand(ACTION_DISCONNECT)`：取消 orchestrator Job，`stopForeground(STOP_FOREGROUND_REMOVE)`，`stopSelf()`
- Manifest 要求：`FOREGROUND_SERVICE_DATA_SYNC` 权限（API 34+），`android:foregroundServiceType="dataSync"`

---

## 10. UI 设计（Jetpack Compose）

### ConnectionScreen
- `OutlinedTextField`：服务器 IP / 主机名（持久化）
- `OutlinedTextField`：控制端口（默认 23010）、数据端口（默认 23011）
- `OutlinedTextField`：节点 ID（UUID，可编辑，持久化）
- 连接按钮：启动 `MediaNodeService(ACTION_CONNECT)`
- 状态徽章：未连接 / 连接中… / 连接失败（原因）
- 连接成功后自动导航至 NodeStatusScreen

### NodeStatusScreen
- `Card`：task_id、视频名、文件大小、服务器地址
- 阶段标签 + `LinearProgressIndicator`：接收中 N% / 处理中（裁剪/合并/压缩）/ 上传中 N%
- `AssistChip`：当前 `TaskState` 名称（颜色编码）
- 错误横幅（仅 Error 状态显示）+ 重试按钮
- FAB：**断开连接** → `ACTION_DISCONNECT` → 返回 ConnectionScreen

---

## 11. 依赖清单

| 依赖 | 用途 |
|------|------|
| `kotlinx-serialization-json` | 协议消息 JSON 序列化 |
| `kotlinx-coroutines-android` | 非阻塞 Socket I/O、Pipeline |
| `androidx.media3:media3-transformer` | 硬件加速视频压缩 |
| `androidx.media3:media3-common` | Media3 公共类 |
| `androidx.room:room-runtime/ktx` | 本地任务 + 分片状态持久化 |
| `androidx.room:room-compiler` (KSP) | Room 代码生成 |
| `androidx.datastore:datastore-preferences` | 节点偏好设置 |
| `androidx.lifecycle:lifecycle-viewmodel-compose` | ViewModel 集成 |
| `androidx.lifecycle:lifecycle-service` | LifecycleService 基类 |

---

## 12. 关键设计决策

1. **pass-through 裁剪 vs 转码裁剪**：`SegmentCutter` 默认 pass-through（最快，无质量损失），仅在非 IDR 起点时退回 Media3 `Transformer + ClippingConfiguration`。
2. **单次转码原则**：整个流水线只在压缩阶段进行一次转码，避免多次编解码损耗。
3. **result.json 与 Python 端一致**：字段结构严格遵循 `export_data_design.md` §3，服务端无需修改验收逻辑。
4. **request_id 幂等**：所有控制消息携带 `request_id`（UUID），服务端和节点双方均需缓存最近 N 条处理结果用于去重。
5. **双通道均就绪才允许任务下发**：`SocketConnectionManager` 在两条连接均 Open 之前不发出 `ConnectionState.Ready`。

