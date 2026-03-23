package osp.leobert.androd.mediaservice.domain.model

/**
 * Metadata about the video file to be transferred from the Python server.
 * Mirrors the TASK_ASSIGN.video_meta payload from the socket protocol.
 */
data class VideoMeta(
    val videoName: String,
    val fileSizeBytes: Long,
    val totalChunks: Int,
    /** SHA-256 hex of the full assembled file, used for integrity verification. */
    val fileHash: String,
)

