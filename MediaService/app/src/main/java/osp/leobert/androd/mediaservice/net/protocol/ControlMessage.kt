package osp.leobert.androd.mediaservice.net.protocol

import com.google.gson.annotations.SerializedName

/**
 * Control channel messages (port 23010, newline-delimited JSON).
 *
 * Each message carries a [requestId] (UUID string) for idempotent processing.
 * The [type] discriminator is used for polymorphic deserialization.
 *
 * Matches SDS/socket_server_node_coordination_design.md §5–§6.
 */
sealed class ControlMessage {

    abstract val requestId: String
    abstract val type: String

    // ── Node → Server ──────────────────────────────────────────────────────

    /**
     * Sent immediately after both channels connect.
     * [currentTask] is non-null when the node crashed mid-task and is recovering.
     */
    data class Hello(
        @SerializedName("requestId")
        override val requestId: String,
        @SerializedName("type")
        override val type: String = "HELLO",
        @SerializedName("nodeId")
        val nodeId: String,
        @SerializedName("nodeVersion")
        val nodeVersion: String,
        @SerializedName("capabilities")
        val capabilities: NodeCapabilities,
        @SerializedName("currentTask")
        val currentTask: CurrentTaskSnapshot? = null,
    ) : ControlMessage()

    data class NodeCapabilities(
        @SerializedName("gpu")
        val gpu: Boolean,
        @SerializedName("codec")
        val codec: List<String>,   // e.g. ["hevc", "avc"]
    )

    data class CurrentTaskSnapshot(
        @SerializedName("taskId")
        val taskId: String,
        @SerializedName("status")
        val status: String,
        @SerializedName("progress")
        val progress: Float,
    )

    /**
     * Sent in response to a [TaskStatusQuery], or proactively every 30 seconds.
     */
    data class TaskStatusReport(
        @SerializedName("requestId")
        override val requestId: String,
        @SerializedName("type")
        override val type: String = "TASK_STATUS_REPORT",
        @SerializedName("taskId")
        val taskId: String,
        @SerializedName("status")
        val status: String,
        @SerializedName("progress")
        val progress: Float,
        @SerializedName("stage")
        val stage: String? = null,
        @SerializedName("lastError")
        val lastError: String? = null,
    ) : ControlMessage()

    /**
     * Lightweight keepalive sent periodically by node.
     */
    data class Heartbeat(
        @SerializedName("requestId")
        override val requestId: String,
        @SerializedName("type")
        override val type: String = "HEARTBEAT",
        @SerializedName("sentAt")
        val sentAt: String? = null,
    ) : ControlMessage()

    /**
     * Acknowledges a peer heartbeat.
     */
    data class HeartbeatAck(
        @SerializedName("requestId")
        override val requestId: String,
        @SerializedName("type")
        override val type: String = "HEARTBEAT_ACK",
        @SerializedName("replyToRequestId")
        val replyToRequestId: String? = null,
        @SerializedName("receivedAt")
        val receivedAt: String? = null,
    ) : ControlMessage()

    /**
     * Lightweight ping used by Python heartbeat watchdogs.
     */
    data class Ping(
        @SerializedName("requestId")
        override val requestId: String,
        @SerializedName("type")
        override val type: String = "PING",
        @SerializedName("sentAt")
        val sentAt: String? = null,
    ) : ControlMessage()

    /**
     * Response for [Ping].
     */
    data class Pong(
        @SerializedName("requestId")
        override val requestId: String,
        @SerializedName("type")
        override val type: String = "PONG",
        @SerializedName("replyToRequestId")
        val replyToRequestId: String? = null,
        @SerializedName("sentAt")
        val sentAt: String? = null,
    ) : ControlMessage()

    /**
     * Accept or reject a [TaskAssign].
     */
    data class TaskConfirm(
        @SerializedName("requestId")
        override val requestId: String,
        @SerializedName("type")
        override val type: String = "TASK_CONFIRM",
        @SerializedName("taskId")
        val taskId: String,
        @SerializedName("accepted")
        val accepted: Boolean,
        @SerializedName("reason")
        val reason: String? = null,
    ) : ControlMessage()

    // ── Server → Node ──────────────────────────────────────────────────────

    data class HelloAck(
        @SerializedName("requestId")
        override val requestId: String,
        @SerializedName("type")
        override val type: String = "HELLO_ACK",
        @SerializedName("serverTime")
        val serverTime: String,
        @SerializedName("syncActions")
        val syncActions: List<SyncAction> = emptyList(),
    ) : ControlMessage()

    data class SyncAction(
        @SerializedName("action")
        val action: String,   // "RESUME_UPLOAD" | "QUERY_PROGRESS"
        @SerializedName("taskId")
        val taskId: String,
    )

    data class TaskAssign(
        @SerializedName("requestId")
        override val requestId: String,
        @SerializedName("type")
        override val type: String = "TASK_ASSIGN",
        @SerializedName("taskId")
        val taskId: String,
        @SerializedName("videoMeta")
        val videoMeta: VideoMetaPayload,
        @SerializedName("processingParams")
        val processingParams: ProcessingParamsPayload,
        @SerializedName("resultRequirements")
        val resultRequirements: ResultRequirements,
    ) : ControlMessage()

    data class VideoMetaPayload(
        @SerializedName("videoName")
        val videoName: String,
        @SerializedName("fileSizeBytes")
        val fileSizeBytes: Long,
        @SerializedName("totalChunks")
        val totalChunks: Int,
        @SerializedName("fileHash")
        val fileHash: String,
    )

    data class ProcessingParamsPayload(
        @SerializedName("segments")
        val segments: List<SegmentPayload>,
        @SerializedName("codecHint")
        val codecHint: String = "hevc",
        /** Override output bitrate in kbps; 0 = derive from input + resolution policy. */
        @SerializedName("targetBitrateKbps")
        val targetBitrateKbps: Int = 0,
    )

    data class SegmentPayload(
        @SerializedName("startMs")
        val startMs: Long,
        @SerializedName("endMs")
        val endMs: Long,
        /** "interesting" | "uninteresting" | "unlabeled". Android only processes "interesting". */
        @SerializedName("label")
        val label: String = "interesting",
    )

    data class ResultRequirements(
        @SerializedName("includeResultJson")
        val includeResultJson: Boolean = true,
        @SerializedName("includeLog")
        val includeLog: Boolean = true,
    )

    data class TaskStatusQuery(
        @SerializedName("requestId")
        override val requestId: String,
        @SerializedName("type")
        override val type: String = "TASK_STATUS_QUERY",
        @SerializedName("taskId")
        val taskId: String,
    ) : ControlMessage()
}

