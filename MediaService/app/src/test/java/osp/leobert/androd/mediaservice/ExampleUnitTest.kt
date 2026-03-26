package osp.leobert.androd.mediaservice

import androidx.media3.common.MimeTypes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import osp.leobert.androd.mediaservice.domain.model.VideoSegment
import osp.leobert.androd.mediaservice.media.pipeline.MediaPipelinePlanner
import osp.leobert.androd.mediaservice.net.protocol.ControlMessage
import osp.leobert.androd.mediaservice.net.protocol.MessageFramer

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

    @Test
    fun decodeControl_supportsHeartbeatMessages() {
        val heartbeatAck = MessageFramer.decodeControl(
            """{"requestId":"ack-1","type":"HEARTBEAT_ACK","replyToRequestId":"hb-1","receivedAt":"2026-03-25T10:00:00Z"}"""
        )
        val ping = MessageFramer.decodeControl(
            """{"requestId":"ping-1","type":"PING","sentAt":"2026-03-25T10:00:01Z"}"""
        )
        val pong = MessageFramer.decodeControl(
            """{"requestId":"pong-1","type":"PONG","replyToRequestId":"ping-1","sentAt":"2026-03-25T10:00:02Z"}"""
        )

        assertTrue(heartbeatAck is ControlMessage.HeartbeatAck)
        assertTrue(ping is ControlMessage.Ping)
        assertTrue(pong is ControlMessage.Pong)
    }

    @Test
    fun taskFailureProtocol_roundTripsExpectedTypes() {
        val encoded = MessageFramer.encodeControl(
            ControlMessage.TaskFailureReport(
                requestId = "fail-1",
                taskId = "task-42",
                failedStage = "PROCESSING",
                reason = "encoder stalled",
                sentAt = "2026-03-26T10:00:00Z",
            )
        )
        val decodedAck = MessageFramer.decodeControl(
            """{"requestId":"ack-42","type":"TASK_FAILURE_ACK","taskId":"task-42","accepted":true,"message":"queued next task"}"""
        )

        assertTrue(encoded.contains("\"type\":\"TASK_FAILURE_REPORT\""))
        assertTrue(encoded.contains("\"taskId\":\"task-42\""))
        assertTrue(decodedAck is ControlMessage.TaskFailureAck)
    }
}