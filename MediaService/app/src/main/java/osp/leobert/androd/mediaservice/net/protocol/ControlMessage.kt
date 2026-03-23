package osp.leobert.androd.mediaservice.net.protocol

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Control channel messages (port 23010, newline-delimited JSON).
 *
 * Each message carries a [requestId] (UUID string) for idempotent processing.
 * The [type] discriminator is used for polymorphic deserialization.
 *
 * Matches SDS/socket_server_node_coordination_design.md §5–§6.
 */
@Serializable
sealed class ControlMessage {

    abstract val requestId: String
    abstract val type: String

    // ── Node → Server ──────────────────────────────────────────────────────

    /**
     * Sent immediately after both channels connect.
     * [currentTask] is non-null when the node crashed mid-task and is recovering.
     */
    @Serializable
    @SerialName("HELLO")
    data class Hello(
        override val requestId: String,
        override val type: String = "HELLO",
        val nodeId: String,
        val nodeVersion: String,
        val capabilities: NodeCapabilities,
        val currentTask: CurrentTaskSnapshot? = null,
    ) : ControlMessage()

    @Serializable
    data class NodeCapabilities(
        val gpu: Boolean,
        val codec: List<String>,   // e.g. ["hevc", "avc"]
    )

    @Serializable
    data class CurrentTaskSnapshot(
        val taskId: String,
        val status: String,
        val progress: Float,
    )

    /**
     * Sent in response to a [TaskStatusQuery], or proactively every 30 seconds.
     */
    @Serializable
    @SerialName("TASK_STATUS_REPORT")
    data class TaskStatusReport(
        override val requestId: String,
        override val type: String = "TASK_STATUS_REPORT",
        val taskId: String,
        val status: String,
        val progress: Float,
        val stage: String? = null,
        val lastError: String? = null,
    ) : ControlMessage()

    /**
     * Accept or reject a [TaskAssign].
     */
    @Serializable
    @SerialName("TASK_CONFIRM")
    data class TaskConfirm(
        override val requestId: String,
        override val type: String = "TASK_CONFIRM",
        val taskId: String,
        val accepted: Boolean,
        val reason: String? = null,
    ) : ControlMessage()

    // ── Server → Node ──────────────────────────────────────────────────────

    @Serializable
    @SerialName("HELLO_ACK")
    data class HelloAck(
        override val requestId: String,
        override val type: String = "HELLO_ACK",
        val serverTime: String,
        val syncActions: List<SyncAction> = emptyList(),
    ) : ControlMessage()

    @Serializable
    data class SyncAction(
        val action: String,   // "RESUME_UPLOAD" | "QUERY_PROGRESS"
        val taskId: String,
    )

    @Serializable
    @SerialName("TASK_ASSIGN")
    data class TaskAssign(
        override val requestId: String,
        override val type: String = "TASK_ASSIGN",
        val taskId: String,
        val videoMeta: VideoMetaPayload,
        val processingParams: ProcessingParamsPayload,
        val resultRequirements: ResultRequirements,
    ) : ControlMessage()

    @Serializable
    data class VideoMetaPayload(
        val videoName: String,
        val fileSizeBytes: Long,
        val totalChunks: Int,
        val fileHash: String,
    )

    @Serializable
    data class ProcessingParamsPayload(
        val segments: List<SegmentPayload>,
        val codecHint: String = "hevc",
        /** Override output bitrate in kbps; 0 = derive from input + resolution policy. */
        val targetBitrateKbps: Int = 0,
    )

    @Serializable
    data class SegmentPayload(
        val startMs: Long,
        val endMs: Long,
        /** "interesting" | "uninteresting" | "unlabeled". Android only processes "interesting". */
        val label: String = "interesting",
    )

    @Serializable
    data class ResultRequirements(
        val includeResultJson: Boolean = true,
        val includeLog: Boolean = true,
    )

    @Serializable
    @SerialName("TASK_STATUS_QUERY")
    data class TaskStatusQuery(
        override val requestId: String,
        override val type: String = "TASK_STATUS_QUERY",
        val taskId: String,
    ) : ControlMessage()
}

