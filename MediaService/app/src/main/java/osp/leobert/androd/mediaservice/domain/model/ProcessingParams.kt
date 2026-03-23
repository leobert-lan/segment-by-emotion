package osp.leobert.androd.mediaservice.domain.model

/**
 * Processing instructions received from the Python server alongside the video file.
 *
 * @param segments           All segments from the review step (interesting + uninteresting +
 *                           unlabeled). The MediaPipeline filters to [VideoSegment.LABEL_INTERESTING]
 *                           only before processing.
 * @param codecHint          Preferred output codec ("hevc" or "avc"); default picks HEVC.
 * @param targetBitrateKbps  Override output bitrate in kbps; 0 = auto-derive from input
 *                           resolution and BitratePolicy (never exceeds input bitrate).
 */
data class ProcessingParams(
    val segments: List<VideoSegment>,
    val codecHint: String = "hevc",
    val targetBitrateKbps: Int = 0,
)

