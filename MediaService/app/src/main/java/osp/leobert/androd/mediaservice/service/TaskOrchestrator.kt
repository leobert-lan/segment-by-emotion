package osp.leobert.androd.mediaservice.service

import android.content.Context
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import osp.leobert.androd.mediaservice.domain.model.NodeTask
import osp.leobert.androd.mediaservice.domain.model.ProcessingParams
import osp.leobert.androd.mediaservice.domain.model.VideoMeta
import osp.leobert.androd.mediaservice.domain.model.VideoSegment
import osp.leobert.androd.mediaservice.domain.state.ProcessingStage
import osp.leobert.androd.mediaservice.domain.state.TaskState
import osp.leobert.androd.mediaservice.media.pipeline.MediaPipeline
import osp.leobert.androd.mediaservice.net.protocol.ControlMessage
import osp.leobert.androd.mediaservice.net.protocol.DataMessage
import osp.leobert.androd.mediaservice.net.socket.DataChannelClient
import osp.leobert.androd.mediaservice.net.socket.SocketConnectionManager
import osp.leobert.androd.mediaservice.storage.db.AppDatabase
import osp.leobert.androd.mediaservice.storage.file.FileStoreManager
import osp.leobert.androd.mediaservice.storage.prefs.NodePreferences
import java.time.Instant

/**
 * Drives the full node lifecycle: connect → receive → process → upload.
 *
 * Single state writer: all [TaskState] transitions happen exclusively here.
 * Call [run] from a long-lived coroutine scope (e.g. MediaNodeService.lifecycleScope).
 * Cancel the coroutine to shut down cleanly.
 */
class TaskOrchestrator(
    private val context: Context,
    private val prefs: NodePreferences,
    private val db: AppDatabase,
    private val fileStore: FileStoreManager,
    private val connectionManager: SocketConnectionManager,
    private val pipeline: MediaPipeline,
) {

    companion object {
        private const val TAG = "TaskOrchestrator"
        private const val CHUNK_SIZE_BYTES = 1 * 1024 * 1024  // 1 MB
    }

    private val _taskState = MutableStateFlow<TaskState>(TaskState.Idle)
    val taskState: StateFlow<TaskState> = _taskState

    private var currentTask: NodeTask? = null

    suspend fun run() {
        // Check for incomplete task from a previous session (cold-start recovery)
        val pending = db.taskDao().getPendingTask()
        if (pending != null) {
            Log.i(TAG, "Resuming incomplete task: ${pending.taskId}")
            // Will send TRANSFER_RESUME_REQUEST after HELLO_ACK
        }

        val host = prefs.serverHost.first()
        val controlPort = prefs.controlPort.first()
        val dataPort = prefs.dataPort.first()

        _taskState.value = TaskState.Connecting(host, controlPort, dataPort)
        connectionManager.connectWithRetry()

        val ctrl = connectionManager.controlChannel ?: run {
            _taskState.value = TaskState.Error(null, "Control channel unavailable", recoverable = true)
            return
        }

        // Listen for HELLO_ACK and process sync_actions
        ctrl.incomingMessages.collect { msg ->
            when (msg) {
                is ControlMessage.HelloAck -> handleHelloAck(msg)
                is ControlMessage.TaskAssign -> handleTaskAssign(msg)
                is ControlMessage.TaskStatusQuery -> handleStatusQuery(msg)
                else -> Unit
            }
        }
    }

    private suspend fun handleHelloAck(ack: ControlMessage.HelloAck) {
        Log.d(TAG, "HELLO_ACK received, sync_actions=${ack.syncActions.size}")
        ack.syncActions.forEach { action ->
            when (action.action) {
                "RESUME_UPLOAD" -> resumeUpload(action.taskId)
                "QUERY_PROGRESS" -> reportProgress(action.taskId)
                else -> Log.w(TAG, "Unknown sync_action: ${action.action}")
            }
        }
        if (ack.syncActions.isEmpty()) {
            _taskState.value = TaskState.AwaitingTask
        }
    }

    private suspend fun handleTaskAssign(msg: ControlMessage.TaskAssign) {
        val taskId = msg.taskId
        Log.i(TAG, "TASK_ASSIGN received: $taskId")

        val meta = VideoMeta(
            videoName = msg.videoMeta.videoName,
            fileSizeBytes = msg.videoMeta.fileSizeBytes,
            totalChunks = msg.videoMeta.totalChunks,
            fileHash = msg.videoMeta.fileHash,
        )
        val params = ProcessingParams(
            segments = msg.processingParams.segments.map {
                VideoSegment(it.startMs, it.endMs, it.label)
            },
            codecHint = msg.processingParams.codecHint,
            targetBitrateKbps = msg.processingParams.targetBitrateKbps,
        )
        currentTask = NodeTask(taskId, meta, params)

        // Confirm acceptance
        connectionManager.controlChannel?.send(
            ControlMessage.TaskConfirm(
                requestId = java.util.UUID.randomUUID().toString(),
                taskId = taskId,
                accepted = true,
            )
        )

        _taskState.value = TaskState.Receiving(taskId, meta.videoName, 0f)
        awaitTransferAndProcess(taskId, meta, params)
    }

    private suspend fun awaitTransferAndProcess(
        taskId: String,
        meta: VideoMeta,
        params: ProcessingParams,
    ) {
        // Data channel events drive progress; when TransferComplete arrives, run pipeline.
        connectionManager.dataChannel?.dataEvents?.collect { event ->
            when (event) {
                is DataMessage.TransferComplete -> {
                    val assembled = runCatching {
                        fileStore.assembleFile(taskId, meta.totalChunks)
                    }.getOrElse { e ->
                        _taskState.value = TaskState.Error(taskId, e.message ?: "Assembly failed", recoverable = false)
                        return@collect
                    }
                    val hash = fileStore.sha256Hex(assembled)
                    if (hash != meta.fileHash) {
                        _taskState.value = TaskState.Error(taskId, "File hash mismatch", recoverable = false)
                        return@collect
                    }
                    runProcessingAndUpload(taskId, params)
                }
                else -> Unit
            }
        }
    }

    private suspend fun runProcessingAndUpload(taskId: String, params: ProcessingParams) {
        _taskState.value = TaskState.Processing(taskId, ProcessingStage.CUTTING, 0f)
        val resultFile = runCatching {
            pipeline.execute(taskId, params) { stage, progress ->
                val pipelineStage = when (stage) {
                    "cutting"     -> ProcessingStage.CUTTING
                    "merging"     -> ProcessingStage.MERGING
                    "transcoding" -> ProcessingStage.TRANSCODING
                    else          -> ProcessingStage.COMPRESSING
                }
                _taskState.value = TaskState.Processing(taskId, pipelineStage, progress)
            }
        }.getOrElse { e ->
            _taskState.value = TaskState.Error(taskId, e.message ?: "Pipeline failed", recoverable = false)
            return
        }

        _taskState.value = TaskState.Uploading(taskId, 0f)
        UploadManager(connectionManager.dataChannel!!, fileStore)
            .upload(taskId, resultFile) { progress ->
                _taskState.value = TaskState.Uploading(taskId, progress)
            }
        _taskState.value = TaskState.Done(taskId)
        db.taskDao().updateStatus(taskId, "Done", Instant.now().toString())
    }

    private suspend fun handleStatusQuery(msg: ControlMessage.TaskStatusQuery) {
        val state = _taskState.value
        val report = ControlMessage.TaskStatusReport(
            requestId = java.util.UUID.randomUUID().toString(),
            taskId = msg.taskId,
            status = state::class.simpleName ?: "Unknown",
            progress = when (state) {
                is TaskState.Receiving -> state.progress
                is TaskState.Processing -> state.progress
                is TaskState.Uploading -> state.progress
                else -> 0f
            },
            stage = (state as? TaskState.Processing)?.stage?.name,
        )
        connectionManager.controlChannel?.send(report)
    }

    private suspend fun reportProgress(taskId: String) {
        handleStatusQuery(
            ControlMessage.TaskStatusQuery(
                requestId = java.util.UUID.randomUUID().toString(),
                taskId = taskId,
            )
        )
    }

    private suspend fun resumeUpload(taskId: String) {
        // TODO: implement resume upload from staged result files
        Log.i(TAG, "Resume upload requested for $taskId")
    }
}

