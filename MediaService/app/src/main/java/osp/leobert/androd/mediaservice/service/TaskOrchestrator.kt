package osp.leobert.androd.mediaservice.service

import android.annotation.SuppressLint
import android.util.Log
import com.google.gson.Gson
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
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
import osp.leobert.androd.mediaservice.net.socket.SocketConnectionManager
import osp.leobert.androd.mediaservice.storage.db.AppDatabase
import osp.leobert.androd.mediaservice.storage.entity.LocalTaskEntity
import osp.leobert.androd.mediaservice.storage.file.FileStoreManager
import osp.leobert.androd.mediaservice.storage.prefs.NodePreferences
import java.io.File
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
    private val onHelloCurrentTaskChanged: (ControlMessage.CurrentTaskSnapshot?) -> Unit = {},
) {

    companion object {
        private const val TAG = "TaskOrchestrator"
    }

    private val gson = Gson()
    private val orchestratorScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val _taskState = MutableStateFlow<TaskState>(TaskState.Idle)
    val taskState: StateFlow<TaskState> = _taskState

    private var currentTask: NodeTask? = null
    private var pendingRecovery: PendingRecovery? = null
    private var recoveryJob: Job? = null

    private enum class RecoveryAction {
        RESUME_UPLOAD,
        REPROCESS_AND_UPLOAD,
        WAIT_FOR_SERVER,
    }

    private data class PendingRecovery(
        val task: NodeTask,
        val persistedStatus: String,
        val initialState: TaskState,
        val action: RecoveryAction,
    )

    private data class StatusSnapshot(
        val status: String,
        val progress: Float,
        val stage: String?,
        val lastError: String?,
    )

    private fun emitState(state: TaskState) {
        _taskState.value = state
        onHelloCurrentTaskChanged(currentTaskSnapshotFor(state))
    }

    private fun currentTaskSnapshotFor(state: TaskState): ControlMessage.CurrentTaskSnapshot? = when (state) {
        is TaskState.Receiving -> ControlMessage.CurrentTaskSnapshot(state.taskId, "Receiving", state.progress)
        is TaskState.Processing -> ControlMessage.CurrentTaskSnapshot(state.taskId, "Processing", state.progress)
        is TaskState.Uploading -> ControlMessage.CurrentTaskSnapshot(state.taskId, "Uploading", state.progress)
        is TaskState.Error -> state.taskId?.let { ControlMessage.CurrentTaskSnapshot(it, "Error", 0f) }
        else -> null
    }

    suspend fun run() {
        try {
            val pending = withContext(Dispatchers.IO) { db.taskDao().getPendingTask() }
            pendingRecovery = pending?.let { buildPendingRecovery(it) }
            pendingRecovery?.let { recovery ->
                currentTask = recovery.task
                Log.i(
                    TAG,
                    "Recovered unfinished task: ${recovery.task.taskId}, persistedStatus=${recovery.persistedStatus}, action=${recovery.action}",
                )
                emitState(recovery.initialState)
            }

            val host = prefs.serverHost.first()
            val controlPort = prefs.controlPort.first()
            val dataPort = prefs.dataPort.first()

            if (pendingRecovery == null) {
                emitState(TaskState.Connecting(host, controlPort, dataPort))
            } else {
                Log.i(TAG, "Connecting with recovered task kept visible in UI")
            }
            connectionManager.connectWithRetry()

            val ctrl = connectionManager.controlChannel ?: run {
                emitState(TaskState.Error(null, "Control channel unavailable", recoverable = true))
                return
            }

            Log.i(TAG, "Control channel ready; waiting control messages")
            ctrl.incomingMessages.collect { msg ->
                when (msg) {
                    is ControlMessage.HelloAck -> handleHelloAck(msg)
                    is ControlMessage.TaskAssign -> handleTaskAssign(msg)
                    is ControlMessage.TaskStatusQuery -> handleStatusQuery(msg)
                    else -> Unit
                }
            }
        } finally {
            Log.i(TAG, "Orchestrator stopping; disconnecting socket channels")
            runCatching { onHelloCurrentTaskChanged(null) }
            orchestratorScope.cancel()
            runCatching { connectionManager.disconnect() }
        }
    }

    private suspend fun handleHelloAck(ack: ControlMessage.HelloAck) {
        Log.d(TAG, "HELLO_ACK received, sync_actions=${ack.syncActions.size}")
        var recoveryScheduled = false
        ack.syncActions.forEach { action ->
            when (action.action) {
                "RESUME_UPLOAD" -> {
                    recoveryScheduled = resumeUpload(action.taskId) || recoveryScheduled
                }
                "QUERY_PROGRESS" -> reportProgress(action.taskId)
                else -> Log.w(TAG, "Unknown sync_action: ${action.action}")
            }
        }
        recoveryScheduled = schedulePendingRecoveryIfNeeded() || recoveryScheduled

        // HELLO_ACK 代表控制面已握手完成；没有恢复任务时再退出 Connecting。
        if (!recoveryScheduled && _taskState.value is TaskState.Connecting) {
            emitState(TaskState.AwaitingTask)
        }
    }

    private suspend fun buildPendingRecovery(persisted: LocalTaskEntity): PendingRecovery? = withContext(Dispatchers.IO) {
        val payload = runCatching {
            gson.fromJson(persisted.processingParamsJson, ControlMessage.ProcessingParamsPayload::class.java)
        }.getOrElse { e ->
            Log.w(TAG, "Failed to restore processing params for ${persisted.taskId}: ${e.message}", e)
            return@withContext null
        }

        val params = ProcessingParams(
            segments = payload.segments.map { VideoSegment(it.startMs, it.endMs, it.label) },
            codecHint = payload.codecHint,
            targetBitrateKbps = payload.targetBitrateKbps,
        )
        val task = NodeTask(
            taskId = persisted.taskId,
            videoMeta = VideoMeta(
                videoName = persisted.videoName,
                fileSizeBytes = persisted.fileSizeBytes,
                totalChunks = persisted.totalChunks,
                fileHash = persisted.fileHash,
            ),
            processingParams = params,
        )

        val resultExists = fileStore.resultVideoFile(persisted.taskId).exists()
        val assembledExists = fileStore.assembledFile(persisted.taskId).exists()
        val action = when {
            resultExists -> RecoveryAction.RESUME_UPLOAD
            persisted.status == "Processing" && assembledExists -> RecoveryAction.REPROCESS_AND_UPLOAD
            else -> RecoveryAction.WAIT_FOR_SERVER
        }
        val initialState = when (persisted.status) {
            "Receiving" -> TaskState.Receiving(
                taskId = persisted.taskId,
                videoName = persisted.videoName,
                progress = receivedProgressOf(persisted.taskId, persisted.totalChunks),
            )
            "Processing" -> TaskState.Processing(persisted.taskId, ProcessingStage.TRANSCODING, 0f)
            "Uploading" -> TaskState.Uploading(persisted.taskId, 0f)
            "Error" -> TaskState.Error(
                taskId = persisted.taskId,
                reason = persisted.errorMessage ?: "Recovered failed task",
                recoverable = action != RecoveryAction.WAIT_FOR_SERVER,
            )
            else -> TaskState.Error(
                taskId = persisted.taskId,
                reason = persisted.errorMessage ?: "Recovered task in status ${persisted.status}",
                recoverable = action != RecoveryAction.WAIT_FOR_SERVER,
            )
        }

        PendingRecovery(
            task = task,
            persistedStatus = persisted.status,
            initialState = initialState,
            action = action,
        )
    }

    private fun receivedProgressOf(taskId: String, totalChunks: Int): Float {
        if (totalChunks <= 0) return 0f
        val received = (0 until totalChunks).count { index -> fileStore.chunkFile(taskId, index).exists() }
        return (received.toFloat() / totalChunks).coerceIn(0f, 0.999f)
    }

    private fun schedulePendingRecoveryIfNeeded(): Boolean {
        val recovery = pendingRecovery ?: return false
        return launchRecovery(recovery)
    }

    private suspend fun resumeUpload(taskId: String): Boolean {
        val recovery = pendingRecovery?.takeIf { it.task.taskId == taskId }
            ?: withContext(Dispatchers.IO) { db.taskDao().getById(taskId) }?.let { buildPendingRecovery(it) }
        if (recovery == null) {
            Log.w(TAG, "Resume requested for unknown task $taskId")
            return false
        }
        pendingRecovery = recovery
        currentTask = recovery.task
        return launchRecovery(recovery)
    }

    private fun launchRecovery(recovery: PendingRecovery): Boolean {
        if (recovery.action == RecoveryAction.WAIT_FOR_SERVER) {
            Log.i(TAG, "Recovered task ${recovery.task.taskId} is waiting for server-side follow-up")
            return false
        }
        if (recoveryJob?.isActive == true) {
            val activeTaskId = pendingRecovery?.task?.taskId
            if (activeTaskId == recovery.task.taskId) {
                Log.i(TAG, "Recovery already running for ${recovery.task.taskId}")
                return true
            }
            recoveryJob?.cancel()
        }

        currentTask = recovery.task
        pendingRecovery = recovery
        recoveryJob = orchestratorScope.launch {
            when (recovery.action) {
                RecoveryAction.RESUME_UPLOAD -> resumeRecoveredUpload(recovery.task.taskId)
                RecoveryAction.REPROCESS_AND_UPLOAD -> runProcessingAndUpload(
                    recovery.task.taskId,
                    recovery.task.processingParams,
                )
                RecoveryAction.WAIT_FOR_SERVER -> Unit
            }
        }
        return true
    }

    @SuppressLint("NewApi") // minSdk=31 > API 26 required by Instant.now()
    private suspend fun handleTaskAssign(msg: ControlMessage.TaskAssign) {
        val taskId = msg.taskId
        Log.i(TAG, "TASK_ASSIGN received: $taskId")
        recoveryJob?.cancel()
        pendingRecovery = null

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
                    processingParamsJson = gson.toJson(msg.processingParams),
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

        emitState(TaskState.Receiving(taskId, meta.videoName, 0f))
        awaitTransferAndProcess(taskId, meta, params)
    }

    private suspend fun awaitTransferAndProcess(
        taskId: String,
        meta: VideoMeta,
        params: ProcessingParams,
    ) {
        val dataEvents = connectionManager.dataChannel?.dataEvents ?: run {
            emitState(TaskState.Error(taskId, "Data channel unavailable", recoverable = true))
            return
        }

        var maxChunkIndex = -1
        while (true) {
            val event = dataEvents.first { msg ->
                when (msg) {
                    is DataMessage.Chunk -> msg.taskId == taskId
                    is DataMessage.TransferComplete -> msg.taskId == taskId
                    else -> false
                }
            }
            when (event) {
                is DataMessage.Chunk -> {
                    if (event.chunkIndex > maxChunkIndex) {
                        maxChunkIndex = event.chunkIndex
                        val progress = ((maxChunkIndex + 1).toFloat() / meta.totalChunks)
                            .coerceIn(0f, 0.999f)
                        emitState(TaskState.Receiving(taskId, meta.videoName, progress))
                    }
                }
                is DataMessage.TransferComplete -> {
                    Log.i(TAG, "[$taskId] Transfer complete received, starting assemble")
                    break
                }
                else -> Unit
            }
        }

        val assembled = runCatching {
            withContext(Dispatchers.IO) {
                fileStore.assembleFile(taskId, meta.totalChunks)
            }
        }.getOrElse { e ->
            emitState(TaskState.Error(taskId, e.message ?: "Assembly failed", recoverable = false))
            dbError(taskId, e.message)
            return
        }
        val hash = withContext(Dispatchers.IO) { fileStore.sha256Hex(assembled) }
        if (hash != meta.fileHash) {
            emitState(TaskState.Error(taskId, "File hash mismatch", recoverable = false))
            dbError(taskId, "hash mismatch: expected ${meta.fileHash} got $hash")
            return
        }
        runProcessingAndUpload(taskId, params)
    }

    @SuppressLint("NewApi")
    private suspend fun runProcessingAndUpload(taskId: String, params: ProcessingParams) {
        dbStatus(taskId, "Processing")
        emitState(TaskState.Processing(taskId, ProcessingStage.TRANSCODING, 0f))

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
                emitState(TaskState.Processing(taskId, pipelineStage, progress))
            }
        }.getOrElse { e ->
            Log.e(TAG, "[$taskId] Pipeline failed", e)
            emitState(TaskState.Error(taskId, e.message ?: "Pipeline failed", recoverable = false))
            dbError(taskId, e.message)
            return
        }

        uploadResult(taskId, resultFile)
    }

    private suspend fun resumeRecoveredUpload(taskId: String) {
        val resultFile = fileStore.resultVideoFile(taskId)
        if (!resultFile.exists()) {
            val message = "Result file missing for resume upload"
            emitState(TaskState.Error(taskId, message, recoverable = false))
            dbError(taskId, message)
            return
        }
        uploadResult(taskId, resultFile)
    }

    private suspend fun uploadResult(taskId: String, resultFile: File) {
        dbStatus(taskId, "Uploading")
        emitState(TaskState.Uploading(taskId, 0f))

        runCatching {
            UploadManager(connectionManager.dataChannel!!, fileStore)
                .upload(taskId, resultFile) { progress ->
                    emitState(TaskState.Uploading(taskId, progress))
                }
        }.onFailure { e ->
            Log.e(TAG, "[$taskId] Upload failed", e)
            emitState(TaskState.Error(taskId, e.message ?: "Upload failed", recoverable = true))
            dbError(taskId, e.message)
            return
        }

        completeTask(taskId)
    }

    private suspend fun completeTask(taskId: String) {
        emitState(TaskState.Done(taskId))
        currentTask = null
        pendingRecovery = null
        recoveryJob = null

        // ── Success: mark done, clean up local files ──────────────────────
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
        val snapshot = buildStatusSnapshot(msg.taskId)
        val report = ControlMessage.TaskStatusReport(
            requestId = java.util.UUID.randomUUID().toString(),
            taskId = msg.taskId,
            status = snapshot.status,
            progress = snapshot.progress,
            stage = snapshot.stage,
            lastError = snapshot.lastError,
        )
        connectionManager.controlChannel?.send(report)
    }

    private suspend fun buildStatusSnapshot(taskId: String): StatusSnapshot {
        val state = _taskState.value
        when (state) {
            is TaskState.Receiving -> if (state.taskId == taskId) {
                return StatusSnapshot("Receiving", state.progress, null, null)
            }
            is TaskState.Processing -> if (state.taskId == taskId) {
                return StatusSnapshot("Processing", state.progress, state.stage.name, null)
            }
            is TaskState.Uploading -> if (state.taskId == taskId) {
                return StatusSnapshot("Uploading", state.progress, null, null)
            }
            is TaskState.Done -> if (state.taskId == taskId) {
                return StatusSnapshot("Done", 1f, null, null)
            }
            is TaskState.Error -> if (state.taskId == taskId) {
                return StatusSnapshot("Error", 0f, null, state.reason)
            }
            else -> Unit
        }

        val persisted = withContext(Dispatchers.IO) { db.taskDao().getById(taskId) }
        if (persisted != null) {
            val normalized = when (persisted.status) {
                "Idle", "Connecting" -> "AwaitingTask"
                else -> persisted.status
            }
            return StatusSnapshot(
                status = normalized,
                progress = 0f,
                stage = null,
                lastError = persisted.errorMessage,
            )
        }

        return StatusSnapshot("AwaitingTask", 0f, null, null)
    }

    private suspend fun reportProgress(taskId: String) = handleStatusQuery(
        ControlMessage.TaskStatusQuery(requestId = java.util.UUID.randomUUID().toString(), taskId = taskId)
    )
}
