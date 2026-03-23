package osp.leobert.androd.mediaservice.domain.model

/**
 * Upload result summary sent back to the Python server.
 * JSON structure matches export_data_design.md §3 (task/summary/segments/label_events).
 */
data class ProcessingResult(
    val taskId: String,
    /** Absolute local path to the processed output video. */
    val outputVideoPath: String,
    val outputFileSizeBytes: Long,
    /** result.json content serialized as string (matches export_data_design.md §3). */
    val summaryJson: String,
)

