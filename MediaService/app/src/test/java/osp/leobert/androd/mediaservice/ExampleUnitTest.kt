package osp.leobert.androd.mediaservice

import androidx.media3.common.MimeTypes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import osp.leobert.androd.mediaservice.domain.model.VideoSegment
import osp.leobert.androd.mediaservice.media.pipeline.MediaPipelinePlanner

class ExampleUnitTest {
    @Test
    fun normalizeInterestingSegments_clampsFiltersAndMerges() {
        val normalized = MediaPipelinePlanner.normalizeInterestingSegments(
            segments = listOf(
                VideoSegment(startMs = -500, endMs = 200, label = VideoSegment.LABEL_INTERESTING),
                VideoSegment(startMs = 100, endMs = 500, label = VideoSegment.LABEL_INTERESTING),
                VideoSegment(startMs = 450, endMs = 900, label = VideoSegment.LABEL_INTERESTING),
                VideoSegment(startMs = 1_500, endMs = 1_520, label = VideoSegment.LABEL_INTERESTING),
                VideoSegment(startMs = 1_200, endMs = 2_500, label = VideoSegment.LABEL_UNINTERESTING),
            ),
            durationMs = 1_000,
        )

        assertEquals(1, normalized.size)
        assertEquals(0, normalized.first().startMs)
        assertEquals(900, normalized.first().endMs)
    }

    @Test
    fun normalizeInterestingSegments_returnsEmptyWhenEverythingIsTooShort() {
        val normalized = MediaPipelinePlanner.normalizeInterestingSegments(
            segments = listOf(
                VideoSegment(startMs = 0, endMs = 100, label = VideoSegment.LABEL_INTERESTING),
                VideoSegment(startMs = 120, endMs = 200, label = VideoSegment.LABEL_INTERESTING),
            ),
            durationMs = 5_000,
        )

        assertTrue(normalized.isEmpty())
    }

    @Test
    fun preferredVideoMimeType_respectsCodecHint() {
        assertEquals(MimeTypes.VIDEO_H264, MediaPipelinePlanner.preferredVideoMimeType("avc"))
        assertEquals(MimeTypes.VIDEO_H264, MediaPipelinePlanner.preferredVideoMimeType("h264"))
        assertEquals(MimeTypes.VIDEO_H265, MediaPipelinePlanner.preferredVideoMimeType("hevc"))
    }
}