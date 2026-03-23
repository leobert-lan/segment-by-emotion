package osp.leobert.androd.mediaservice.domain.model

/**
 * A single video segment to be cut, as received from the Python server via ProcessingParams.
 * Times are in milliseconds.
 *
 * @param label  Segment label from the Python review step: [LABEL_INTERESTING],
 *               [LABEL_UNINTERESTING], or [LABEL_UNLABELED].
 *               The MediaPipeline only processes [LABEL_INTERESTING] segments.
 */
data class VideoSegment(
    val startMs: Long,
    val endMs: Long,
    val label: String = LABEL_INTERESTING,
) {
    val durationMs: Long get() = endMs - startMs

    companion object {
        const val LABEL_INTERESTING   = "interesting"
        const val LABEL_UNINTERESTING = "uninteresting"
        const val LABEL_UNLABELED     = "unlabeled"
    }
}

