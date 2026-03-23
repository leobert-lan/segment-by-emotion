# Android 节点通信时序说明

> 时序图文件：`android_node_sequence.puml`（PlantUML）  
> 渲染命令：`plantuml android_node_sequence.puml` 或在 IDE PlantUML 插件中预览。

---

## 一、通信流程七阶段概览

| 阶段 | 关键消息 | 通道 | 触发条件 |
|------|---------|------|---------|
| ① 双通道建立 | TCP SYN/ACK + **HELLO** | :23010 + :23011 | `ACTION_CONNECT` Intent |
| ② 握手同步 | **HELLO_ACK** | :23010 | 双通道均 Connected |
| ③ 任务下发 | **TASK_ASSIGN / TASK_CONFIRM** | :23010 | 服务端调度 |
| ④ 视频下载 | **CHUNK × N / CHUNK_ACK × N / TRANSFER_COMPLETE** | :23011 | TASK_CONFIRM 后 |
| ⑤ 媒体处理 | —（节点内部）— | — | TRANSFER_COMPLETE + hash 校验通过 |
| ⑥ 结果回传 | **RESULT_CHUNK × M / CHUNK_ACK × M / RESULT_TRANSFER_COMPLETE** | :23011 | pipeline.execute 完成 |
| ⑦ 完成清理 | DB 删除 + 文件删除 | — | upload 完成 |

---

## 二、分片大小设计依据（当前值：8 MB）

> 代码位置：`UploadManager.CHUNK_SIZE = 8 * 1024 * 1024`  
> 说明：**下载方向**（服务端 → 节点）的分片大小由 Python 服务端控制，建议与此保持一致。

分片大小是"协议开销 vs. 内存占用 vs. 续传损失"三者之间的权衡：

### 量化模型（stop-and-wait per-chunk）

```
有效吞吐 = chunkSize / (传输耗时 + RTT)
```

| 分片大小 | 千兆 LAN（125 MB/s，RTT=1ms） | 百兆 LAN（12.5 MB/s，RTT=3ms） | 峰值堆内存 | ACK 次数（1 GB 文件） |
|---------|------------------------------|-------------------------------|-----------|---------------------|
| 1 MB    | 111 MB/s（89%）               | 12.1 MB/s（97%）               | ~2 MB     | ~1 024              |
| **8 MB** | **123 MB/s（98%）**          | **12.4 MB/s（99%）**           | **~16 MB** | **~128**           |
| 16 MB   | 124 MB/s（99%）               | 12.5 MB/s（100%）              | ~32 MB    | ~64                 |

### 选择 8 MB 的理由

1. **吃满千兆 LAN**：效率 98%，相比 1 MB（89%）提升显著；再加大到 16 MB 边际收益仅 1%。
2. **内存安全**：`ByteArray(8 MB)` + 尾片副本 ≤ 16 MB 堆占用，Android 典型 256 MB 堆可忽略。
3. **续传损失上界可接受**：断线最多重传 1 个 in-flight chunk（≤ 8 MB），GB 级文件背景下代价极小。
4. **ACK 次数大幅减少**：1 GB 文件仅需 ~128 次 ACK 往返（vs. 1 MB 时的 1 024 次），控制段开销降低 8×。
5. **闪存写入仍在最优区间**：UFS/eMMC 顺序写最优区间 64 KB–16 MB，8 MB 完全在内。

### 适用建议

```
弱 WiFi / 低端机  →  2 MB
百兆 LAN          →  4–8 MB（当前默认）
千兆 LAN          →  8–16 MB
有线万兆 / NVMe   →  16–32 MB
```

---

## 三、数据帧格式详解

### 3.1 数据通道帧（:23011）

```
┌───────────────────┬──────────────────────────────┬────────────────────┐
│  4 字节            │  JSON header（UTF-8）          │  binary payload    │
│  Big-Endian        │  {"type":"CHUNK",              │  ≤ 1 048 576 字节  │
│  header 字节数     │   "chunk_index":N, ...}        │  (可为 0)          │
└───────────────────┴──────────────────────────────┴────────────────────┘
```

实现：`MessageFramer.writeDataFrame()` / `MessageFramer.readDataFrameHeader()`

### 3.2 控制通道帧（:23010）

```
{"type":"TASK_ASSIGN","request_id":"uuid","task_id":"t-0042",...}\n
```

实现：`MessageFramer.encodeControl()` / `MessageFramer.decodeControl()`（newline-delimited JSON）

---

## 四、关键设计决策

### 4.1 上传时先订阅 ChunkAck 再发送（防竞态）

```kotlin
// UploadManager.uploadFile() 核心片段
val ackDeferred = async(start = CoroutineStart.UNDISPATCHED) {
    // UNDISPATCHED：同步执行到 first{} 的第一个挂起点
    // 此时 SharedFlow 订阅已注册，不会丢失 ACK
    dataChannel.dataEvents.first { msg ->
        msg is DataMessage.ChunkAck && msg.chunkIndex == idx
    }
}
dataChannel.writeDataFrame(ResultChunk(...), payload)  // 再发送
withTimeoutOrNull(30_000L) { ackDeferred.await() }     // 最多等 30s
```

**为何必须先订阅？**  
`DataChannelClient.dataEvents` 是 `SharedFlow(replay=0)`，不重播历史值。  
若先发送后订阅，服务端快速响应的 ACK 会在订阅建立前到达，导致永久等待直至超时。

### 4.2 Transformer 必须在主线程运行

Media3 `Transformer` 内部通过 `Util.getCurrentOrMainLooper()` 确定 `applicationLooper`，并在 `start()` 时验证调用线程。  
`MediaPipeline.execute()` 使用 `withContext(Dispatchers.Main)` 切换到主线程后再调用 `transformer.start()`，进度轮询也通过主线程 `Handler` 完成。

### 4.3 NodeStateHolder 单例桥接 Service ↔ ViewModel

`MediaNodeService`（后台 Service）与 `NodeStatusViewModel`（UI 层）之间通过 `NodeStateHolder.state: StateFlow<TaskState>` 共享状态，无需 `BoundService` 或广播。

```
TaskOrchestrator
    → MediaNodeService.orchestrator.taskState.onEach { NodeStateHolder.update(it) }
    → NodeStatusViewModel.taskState = NodeStateHolder.state.stateIn(...)
    → NodeStatusScreen(collectAsState)
```

### 4.4 完成后立即清理

任务成功完成后，下载的原始分片文件（700 MB）+ 组装文件（700 MB）+ 结果文件（~15 MB）全部删除，仅在服务端落盘。节点本地无任何残留，避免存储无限增长。

---

## 五、时序图说明

时序图包含以下路径：

- **正常流**：完整的七阶段端到端流程，含具体消息字段示例
- **异常路径 A**：崩溃恢复（HELLO_ACK.sync_actions 驱动状态恢复）
- **异常路径 B**：传输中断续传（TRANSFER_RESUME_REQUEST + 补发缺失分片）
- **哈希校验失败**：文件完整性不通过时的 Error 状态转换
- **ChunkAck 超时**：上传阶段 30s 超时触发 Error(recoverable=true)
- **TaskState 转换链**：图末尾的完整状态机注释

渲染建议：使用 `plantuml -tpng -DPLANTUML_LIMIT_SIZE=16384 android_node_sequence.puml` 生成高分辨率图（时序图较长，建议调大渲染上限）。

