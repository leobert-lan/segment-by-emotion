package osp.leobert.androd.mediaservice.service

import android.annotation.SuppressLint
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import osp.leobert.androd.mediaservice.domain.model.NodeTask
import osp.leobert.androd.mediaservice.domain.model.ProcessingParams
import osp.leobert.androd.mediaservice.domain.model.VideoMeta
import osp.leobert.androd.mediaservice.domain.model.VideoSegment
import osp.leobert.androd.mediaservice.domain.state.ProcessingStage
import osp.leobert.androd.mediaservice.domain.state.TaskState
import osp.leobert.androd.mediaservice.media.pipeline.MediaPipeline
import osp.leobert.androd.mediaservice.net.protocol.ControlMessage
import osp.leobert.androd.mediaservice.net.protocol.DataMessage
import osp.leobert.androd.mediaservice.net.socket.SocketConnectionManager
import osp.leobert.androd.mediaservice.storage.db.AppDatabase
import osp.leobert.androd.mediaservice.storage.entity.LocalTaskEntity
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
    private val prefs: NodePreferences,
    private val db: AppDatabase,
    private val fileStore: FileStoreManager,
    private val connectionManager: SocketConnectionManager,
    private val pipeline: MediaPipeline,
) {

    companion object {
        private const val TAG = "TaskOrchestrator"
    }

    private val _taskState = MutableStateFlow<TaskState>(TaskState.Idle)
    val taskState: StateFlow<TaskState> = _taskState

    private var currentTask: NodeTask? = null

    suspend fun run() {
        val pending = withContext(Dispatchers.IO) { db.taskDao().getPendingTask() }
        if (pending != null) {
            Log.i(TAG, "Resuming incomplete task: ${pending.taskId}")
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
        if (ack.syncActions.isEmpty()) _taskState.value = TaskState.AwaitingTask
    }

    @SuppressLint("NewApi") // minSdk=31 > API 26 required by Instant.now()
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

        // ── Persist task to Room for crash recovery ───────────────────────
        val now = Instant.now().toString()
        withContext(Dispatchers.IO) {
            db.taskDao().upsert(
                LocalTaskEntity(
                    taskId = taskId,
                    videoName = meta.videoName,
                    fileSizeBytes = meta.fileSizeBytes,
                    totalChunks = meta.totalChunks,
                    fileHash = meta.fileHash,
                    processingParamsJson = Json.encodeToString(msg.processingParams),
                    status = "Receiving",
                    createdAt = now,
                    updatedAt = now,
                )
            )
        }

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
        connectionManager.dataChannel?.dataEvents?.collect { event ->
            when (event) {
                is DataMessage.TransferComplete -> {
                    val assembled = runCatching {
                        withContext(Dispatchers.IO) {
                            fileStore.assembleFile(taskId, meta.totalChunks)
                        }
                    }.getOrElse { e ->
                        _taskState.value = TaskState.Error(taskId, e.message ?: "Assembly failed", recoverable = false)
                        dbError(taskId, e.message)
                        return@collect
                    }
                    val hash = withContext(Dispatchers.IO) { fileStore.sha256Hex(assembled) }
                    if (hash != meta.fileHash) {
                        _taskState.value = TaskState.Error(taskId, "File hash mismatch", recoverable = false)
                        dbError(taskId, "hash mismatch: expected ${meta.fileHash} got $hash")
                        return@collect
                    }
                    runProcessingAndUpload(taskId, params)
                }
                else -> Unit
            }
        }
    }

    @SuppressLint("NewApi")
    private suspend fun runProcessingAndUpload(taskId: String, params: ProcessingParams) {
        dbStatus(taskId, "Processing")
        _taskState.value = TaskState.Processing(taskId, ProcessingStage.TRANSCODING, 0f)

        val resultFile = runCatching {
            pipeline.execute(
                taskId    = taskId,
                videoName = currentTask?.videoMeta?.videoName ?: "unknown.mp4",
                params    = params,
            ) { stage, progress ->
                val pipelineStage = when (stage) {
                    "cutting"     -> ProcessingStage.CUTTING
                    "merging"     -> ProcessingStage.MERGING
                    "transcoding" -> ProcessingStage.TRANSCODING
                    else          -> ProcessingStage.COMPRESSING
                }
                _taskState.value = TaskState.Processing(taskId, pipelineStage, progress)
            }
        }.getOrElse { e ->
            Log.e(TAG, "[$taskId] Pipeline failed", e)
            _taskState.value = TaskState.Error(taskId, e.message ?: "Pipeline failed", recoverable = false)
            dbError(taskId, e.message)
            return
        }

        dbStatus(taskId, "Uploading")
        _taskState.value = TaskState.Uploading(taskId, 0f)

        runCatching {
            UploadManager(connectionManager.dataChannel!!, fileStore)
                .upload(taskId, resultFile) { progress ->
                    _taskState.value = TaskState.Uploading(taskId, progress)
                }
        }.onFailure { e ->
            Log.e(TAG, "[$taskId] Upload failed", e)
            _taskState.value = TaskState.Error(taskId, e.message ?: "Upload failed", recoverable = true)
            dbError(taskId, e.message)
            return
        }

        // ── Success: mark done, clean up local files ──────────────────────
        _taskState.value = TaskState.Done(taskId)
        withContext(Dispatchers.IO) {
            runCatching {
                // Delete task and its cascade-linked chunks from DB
                db.taskDao().delete(taskId)
                // Delete all files for this task (chunks, assembled.mp4, result.mp4, json)
                fileStore.cleanTask(taskId)
                Log.i(TAG, "[$taskId] Task files and DB record cleaned up")
            }.onFailure { e ->
                Log.w(TAG, "[$taskId] Cleanup failed (non-fatal): ${e.message}")
            }
        }
    }

    // ── DB helpers ────────────────────────────────────────────────────────

    @SuppressLint("NewApi")
    private suspend fun dbStatus(taskId: String, status: String) = withContext(Dispatchers.IO) {
        runCatching { db.taskDao().updateStatus(taskId, status, Instant.now().toString()) }
    }

    @SuppressLint("NewApi")
    private suspend fun dbError(taskId: String, message: String?) = withContext(Dispatchers.IO) {
        runCatching {
            db.taskDao().updateStatusWithError(taskId, "Error", message, Instant.now().toString())
        }
    }

    private suspend fun handleStatusQuery(msg: ControlMessage.TaskStatusQuery) {
        val state = _taskState.value
        val report = ControlMessage.TaskStatusReport(
            requestId = java.util.UUID.randomUUID().toString(),
            taskId = msg.taskId,
            status = state::class.simpleName ?: "Unknown",
            progress = when (state) {
                is TaskState.Receiving  -> state.progress
                is TaskState.Processing -> state.progress
                is TaskState.Uploading  -> state.progress
                else -> 0f
            },
            stage = (state as? TaskState.Processing)?.stage?.name,
        )
        connectionManager.controlChannel?.send(report)
    }

    private suspend fun reportProgress(taskId: String) = handleStatusQuery(
        ControlMessage.TaskStatusQuery(requestId = java.util.UUID.randomUUID().toString(), taskId = taskId)
    )

    private fun resumeUpload(taskId: String) {
        Log.i(TAG, "Resume upload requested for $taskId — TODO: implement")
    }
}
