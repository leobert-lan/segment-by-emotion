package osp.leobert.androd.mediaservice.domain.state

/**
 * Processing stage within the [TaskState.Processing] state.
 *
 * - [CUTTING] / [MERGING] / [COMPRESSING]: legacy three-stage pipeline (kept for backward compat).
 * - [TRANSCODING]: unified single-pass Transformer pipeline (clip + merge + encode in one step).
 */
enum class ProcessingStage { CUTTING, MERGING, COMPRESSING, TRANSCODING }

/**
 * The full state machine for the Android node.
 *
 * Single writer: only TaskOrchestrator may mutate the MutableStateFlow<TaskState>.
 * UI and notifications read via StateFlow (read-only).
 *
 * Lifecycle:
 *   Idle → Connecting → [await TASK_ASSIGN] → Receiving → Processing → Uploading → Done
 *                                                                ↕
 *                                                             Error
 */
sealed class TaskState {

    /** No active task; service may or may not be running. */
    data object Idle : TaskState()

    /** Establishing TCP connections to the Python server on both channels. */
    data class Connecting(
        val host: String,
        val controlPort: Int,
        val dataPort: Int,
    ) : TaskState()

    /**
     * Both channels are open; waiting for TASK_ASSIGN from server.
     * Entered after successful HELLO_ACK with no pending resume action.
     */
    data object AwaitingTask : TaskState()

    /**
     * Receiving the video file in chunks from the data channel.
     * [progress] is in [0.0, 1.0].
     */
    data class Receiving(
        val taskId: String,
        val videoName: String,
        val progress: Float,
    ) : TaskState()

    /**
     * MediaPipeline is executing (cut → merge → compress).
     * [progress] is in [0.0, 1.0] within the current [stage].
     */
    data class Processing(
        val taskId: String,
        val stage: ProcessingStage,
        val progress: Float,
    ) : TaskState()

    /**
     * Uploading the processed result file to the server.
     * [progress] is in [0.0, 1.0].
     */
    data class Uploading(
        val taskId: String,
        val progress: Float,
    ) : TaskState()

    /** Task fully completed and confirmed by server. */
    data class Done(val taskId: String) : TaskState()

    /**
     * An error occurred.
     * [recoverable] = true → TaskOrchestrator will attempt reconnect automatically.
     * [recoverable] = false → requires user interaction to reset.
     */
    data class Error(
        val taskId: String?,
        val reason: String,
        val recoverable: Boolean,
    ) : TaskState()
}

