package osp.leobert.androd.mediaservice.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.launch
import osp.leobert.androd.mediaservice.MainActivity
import osp.leobert.androd.mediaservice.domain.state.TaskState
import osp.leobert.androd.mediaservice.media.pipeline.MediaPipeline
import osp.leobert.androd.mediaservice.net.protocol.ControlMessage
import osp.leobert.androd.mediaservice.net.socket.ControlChannelClient
import osp.leobert.androd.mediaservice.net.socket.DataChannelClient
import osp.leobert.androd.mediaservice.net.socket.SocketConnectionManager
import osp.leobert.androd.mediaservice.storage.db.AppDatabase
import osp.leobert.androd.mediaservice.storage.file.FileStoreManager
import osp.leobert.androd.mediaservice.storage.prefs.NodePreferences
import java.time.Instant
import java.util.UUID
import java.util.concurrent.atomic.AtomicReference

/**
 * ForegroundService that hosts the [TaskOrchestrator].
 *
 * Start with ACTION_CONNECT (includes server host/port extras).
 * Stop with ACTION_DISCONNECT.
 *
 * Manifest requirements (already declared in AndroidManifest.xml):
 *   - android.permission.FOREGROUND_SERVICE
 *   - android.permission.FOREGROUND_SERVICE_DATA_SYNC  (API 34+)
 *   - android:foregroundServiceType="dataSync"
 */
class MediaNodeService : LifecycleService() {

    private data class ConnectionConfig(
        val host: String,
        val controlPort: Int,
        val dataPort: Int,
    )

    companion object {
        const val ACTION_CONNECT = "osp.leobert.mediaservice.ACTION_CONNECT"
        const val ACTION_DISCONNECT = "osp.leobert.mediaservice.ACTION_DISCONNECT"
        const val EXTRA_HOST = "host"
        const val EXTRA_CONTROL_PORT = "control_port"
        const val EXTRA_DATA_PORT = "data_port"

        private const val NOTIFICATION_ID = 1
        private const val CHANNEL_ID = "node_processing"
    }

    private var orchestratorJob: Job? = null
    private lateinit var notificationManager: NotificationManager
    private var activeConnectionConfig: ConnectionConfig? = null

    override fun onCreate() {
        super.onCreate()
        notificationManager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        when (intent?.action) {
            ACTION_CONNECT -> handleConnect(intent)
            ACTION_DISCONNECT -> handleDisconnect()
        }
        return START_NOT_STICKY
    }

    private fun handleConnect(intent: Intent) {
        val host = intent.getStringExtra(EXTRA_HOST) ?: return
        val controlPort = intent.getIntExtra(EXTRA_CONTROL_PORT, NodePreferences.DEFAULT_CONTROL_PORT)
        val dataPort = intent.getIntExtra(EXTRA_DATA_PORT, NodePreferences.DEFAULT_DATA_PORT)
        val requestedConfig = ConnectionConfig(host, controlPort, dataPort)

        if (orchestratorJob?.isActive == true && activeConnectionConfig == requestedConfig) {
            return
        }
        if (orchestratorJob?.isActive == true) {
            orchestratorJob?.cancel()
            orchestratorJob = null
        }
        activeConnectionConfig = requestedConfig

        startForeground(NOTIFICATION_ID, buildNotification(TaskState.Connecting(host, controlPort, dataPort)))

        val prefs = NodePreferences(applicationContext)
        val db = AppDatabase.getInstance(applicationContext)
        val fileStore = FileStoreManager(applicationContext)
        val helloCurrentTask = AtomicReference<ControlMessage.CurrentTaskSnapshot?>(null)

        val connectionManager = SocketConnectionManager(
            host = host,
            controlPort = controlPort,
            dataPort = dataPort,
            controlClientFactory = { h, p ->
                ControlChannelClient(h, p) {
                    ControlMessage.Hello(
                        requestId = UUID.randomUUID().toString(),
                        nodeId = "android-node",  // TODO: read from prefs
                        nodeVersion = NodePreferences.DEFAULT_NODE_VERSION,
                        capabilities = ControlMessage.NodeCapabilities(
                            gpu = true,
                            codec = listOf("hevc", "avc"),
                        ),
                        currentTask = helloCurrentTask.get(),
                    )
                }
            },
            dataClientFactory = { h, p ->
                DataChannelClient(
                    host = h,
                    port = p,
                    onChunkReceived = { chunk, payload ->
                        fileStore.writeChunkPayload(chunk.taskId, chunk.chunkIndex, payload)
                        db.chunkDao().markReceived(chunk.taskId, chunk.chunkIndex)
                        db.taskDao().updateTransferId(
                            taskId = chunk.taskId,
                            transferId = chunk.transferId,
                            updatedAt = Instant.now().toString(),
                        )
                        true
                    },
                    onTransferComplete = { /* handled in DataChannelClient dataEvents */ },
                )
            },
        )

        val pipeline = MediaPipeline(applicationContext, fileStore)
        val orchestrator = TaskOrchestrator(
            prefs = prefs,
            db = db,
            fileStore = fileStore,
            connectionManager = connectionManager,
            pipeline = pipeline,
            onHelloCurrentTaskChanged = { helloCurrentTask.set(it) },
        )

        // Mirror state → NodeStateHolder (read by NodeStatusViewModel) AND notification
        orchestrator.taskState.onEach { state ->
            NodeStateHolder.update(state)
            notificationManager.notify(NOTIFICATION_ID, buildNotification(state))
        }.launchIn(lifecycleScope)

        orchestratorJob = lifecycleScope.launch { orchestrator.run() }
    }

    private fun handleDisconnect() {
        orchestratorJob?.cancel()
        orchestratorJob = null
        activeConnectionConfig = null
        NodeStateHolder.update(TaskState.Idle)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    // ── Notification ──────────────────────────────────────────────────────

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "节点处理",
            NotificationManager.IMPORTANCE_LOW,  // silent — no sound
        ).apply { description = "视频处理节点运行状态" }
        notificationManager.createNotificationChannel(channel)
    }

    private fun buildNotification(state: TaskState): android.app.Notification {
        val stopIntent = Intent(this, MediaNodeService::class.java).apply {
            action = ACTION_DISCONNECT
        }
        val stopPendingIntent = PendingIntent.getService(
            this, 0, stopIntent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val (title, body, progress) = when (state) {
            is TaskState.Idle -> Triple("节点待机", "未连接", null)
            is TaskState.Connecting -> Triple("连接中…", "${state.host}:${state.controlPort}", null)
            is TaskState.AwaitingTask -> Triple("已连接", "等待任务下发", null)
            is TaskState.Receiving -> Triple(
                "接收视频", state.videoName,
                (state.progress * 100).toInt()
            )
            is TaskState.Processing -> Triple(
                "处理中 — ${state.stage.name.lowercase()}",
                state.taskId,
                (state.progress * 100).toInt(),
            )
            is TaskState.Uploading -> Triple(
                "上传结果", state.taskId,
                (state.progress * 100).toInt(),
            )
            is TaskState.Done -> Triple("处理完成", state.taskId, 100)
            is TaskState.Error -> Triple(
                if (state.recoverable) "任务中断，等待恢复" else "错误",
                state.taskId?.let { "$it · ${state.reason}" } ?: state.reason,
                null,
            )
        }

        val builder = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setContentTitle(title)
            .setContentText(body)
            .setOngoing(state !is TaskState.Done && (state !is TaskState.Error || state.recoverable))
            .addAction(android.R.drawable.ic_delete, "停止", stopPendingIntent)
            .setContentIntent(
                PendingIntent.getActivity(
                    this, 0,
                    Intent(this, MainActivity::class.java),
                    PendingIntent.FLAG_IMMUTABLE,
                )
            )

        if (progress != null) {
            val indeterminate = progress == 0
            builder.setProgress(100, progress, indeterminate)
        }

        return builder.build()
    }
}

