package osp.leobert.androd.mediaservice.domain.model

/**
 * A task as assigned by the Python server. Combines the TASK_ASSIGN wire payload
 * with local processing fields.
 */
data class NodeTask(
    val taskId: String,
    val videoMeta: VideoMeta,
    val processingParams: ProcessingParams,
    /** Absolute path to the locally assembled input video file (null until download complete). */
    val localInputPath: String? = null,
    /** Absolute path to the processed output video (null until pipeline complete). */
    val localOutputPath: String? = null,
)

