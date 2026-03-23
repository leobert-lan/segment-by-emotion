
现在我有完整的代码图景了，给你一个精确的现状报告。

当前有哪些界面
界面 1：连接配置页（ConnectionScreen）— 已实现
┌─────────────────────────────────┐
│  媒体节点 — 连接配置              │
│                                 │
│  [服务器地址 (IP / 主机名)      ] │
│  [控制端口 ]   [数据端口        ] │
│  [节点 ID（留空自动生成）       ] │
│                                 │
│  <错误提示文字（如有）>           │
│                                 │
│       [ 连 接 ]                 │
└─────────────────────────────────┘
功能：
四个输入项，设置持久化到 DataStore（重启后保留）
点击"连接"：保存配置 → startForegroundService(ACTION_CONNECT) 启动服务 → 导航到状态页
无法从状态页返回（连接后 ConnectionScreen 出栈了）

界面 2：节点状态页（NodeStatusScreen）— UI 有但数据未接通
┌─────────────────────────────────┐
│  节点状态                        │
│  [空闲] ← 状态 Chip              │
│                                 │
│  ┌─────────────────────────┐    │
│  │ 任务 ID: xxx            │    │
│  │ 视频: xxx.mp4           │    │
│  │ 阶段: 转码处理           │    │
│  │ [████████░░░░░] 60%     │    │
│  └─────────────────────────┘    │
│                                 │
│  <错误横幅（如有）>              │
│                                 │
│       [ 断开连接 ]               │
└─────────────────────────────────┘
功能：
UI 代码完整，支持展示所有状态（接收中/处理中/上传中/完成/错误）
阶段中文名：裁剪片段 / 合并片段 / 压缩编码 / 转码处理（新加的）
⚠️ 关键问题：NodeStatusViewModel 的 taskState 是一个写死的 MutableStateFlow(TaskState.Idle)，根本没有连接到 TaskOrchestrator 的实际状态流。状态页永远显示"空闲"。

界面 3：系统通知（MediaNodeService.buildNotification()）— 实际上是唯一能看到实时状态的地方
通知栏反而是接通了的，因为 MediaNodeService 直接持有 orchestrator.taskState 的引用：
状态
通知标题
说明
Connecting
连接中…
无进度条
AwaitingTask
已连接
"等待任务下发"
Receiving
接收视频
有确定进度条
Processing
处理中 — transcoding
有确定进度条
Uploading
上传结果
有确定进度条
Done
处理完成
进度 100%
Error
错误
显示原因
通知上有"停止"按钮（ACTION_DISCONNECT），点击停止服务。

缓存/文件管理策略（实际情况）
存储结构
Context.filesDir/
└── tasks/
└── <taskId>/
├── chunks/
│   ├── chunk_0.bin   (1 MB)
│   ├── chunk_1.bin   (1 MB)
│   └── ...
├── assembled.mp4     ← 所有 chunk 顺序拼接
├── output/
│   └── result_<taskId>.mp4  ← 转码输出
├── result.json       ← 上传用摘要（目前未生成）
└── processing.log    ← 日志（目前未生成）
单 chunk 大小：1 MB（UploadManager 和 TaskOrchestrator 都用 1 MB）
存储位置：Context.filesDir（应用私有内部存储，不需要存储权限）
⚠️ 完成后是否清理缓存
目前完全不清理。 FileStoreManager.cleanTask() 存在但从未被调用：
FileStoreManager.cleanTask() ← 代码有，但在整个工程里没有任何调用点
TaskDao.delete()             ← 代码有，同样从未被调用
ChunkDao.deleteByTask()      ← 代码有，同样从未被调用
任务 Done 之后：
chunks/chunk_*.bin：永久保留
assembled.mp4：永久保留
result_<taskId>.mp4：永久保留
Room DB 记录：永久保留
没有任何大小限制、没有 LRU、没有 TTL、没有用户手动清除入口。
数据库存了什么
local_tasks 表：
| 字段 | 说明 |
|------|------| 
| taskId | 主键 | 
| videoName | 视频文件名 |
| fileSizeBytes | 文件大小 | 
| totalChunks | 总分片数 | 
| fileHash | 完整文件 SHA-256 | 
| processingParamsJson | 片段信息 JSON |
| status | 当前状态字符串 | 
| errorMessage | 错误信息 | 
| createdAt / updatedAt | 时间戳 |


transfer_chunks 表： | 字段 | 说明 | |------|------| | taskId + chunkIndex | 联合主键 | | chunkHash | 分片 SHA-256 | | received | 0/1 是否已收到 | | fileOffset | 在组装文件中的偏移 |

任务管理界面
目前不存在任务管理界面。 没有任务历史列表，没有手动删除任务的入口，没有存储用量显示。
TaskDao 查询单任务恢复用，不支持多任务并发，也没有历史任务列表查询（只有 getPendingTask() 取最近一条未完成的）。

总结：需要补充的缺口
根据上面的分析，有几个明显缺口需要处理：
问题
优先级
说明
NodeStatusViewModel 没有接真实状态流
阻塞性
状态页永远显示空闲，用户看不到进度
任务完成后不清理缓存
高
多任务后存储无限增长
没有任务管理/历史界面
中
无法查看历史、手动清除
result.json 从未生成
高
UploadManager 发送它但 MediaPipeline 没有输出它
UploadManager 没等 ChunkAck
高
有 TODO，上传可靠性存疑
要我优先处理哪个？建议从最高优先级的 状态流接通 + 任务完成清理 开始。