package osp.leobert.androd.mediaservice.media.pipeline

import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import android.util.Log
import osp.leobert.androd.mediaservice.domain.model.VideoSegment
import java.io.File

/**
 * Cuts a single [VideoSegment] from [inputFile] using pass-through muxing.
 * No transcode — fastest path, zero quality loss.
 *
 * **⚠ Format limitation**: [android.media.MediaExtractor] supports MP4, MKV, WebM natively
 * but does **not** support AVI or FLV containers. For full format coverage (including AVI/FLV),
 * use [MediaPipeline] which relies on Media3 Transformer + ExoPlayer extractors.
 *
 * Note: If the segment start does not land on a sync/keyframe, the output
 * may start with non-IDR frames. [MediaPipeline] (Transformer + ClippingConfiguration)
 * is the recommended path for frame-accurate cuts.
 *
 * @deprecated Superseded by [MediaPipeline] which handles all input formats and
 *             performs cut + merge + encode in a single Transformer pass.
 */
class SegmentCutter {

    companion object {
        private const val TAG = "SegmentCutter"
        private const val CHUNK_SIZE = 1 * 1024 * 1024  // 1 MB read buffer
    }

    /**
     * @param inputFile  Assembled source video file
     * @param segment    Time range to cut (milliseconds)
     * @param outputFile Destination file (will be overwritten)
     */
    fun cut(inputFile: File, segment: VideoSegment, outputFile: File) {
        val extractor = MediaExtractor()
        extractor.setDataSource(inputFile.absolutePath)

        val muxer = MediaMuxer(outputFile.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)

        val trackMap = mutableMapOf<Int, Int>() // extractor track → muxer track

        for (i in 0 until extractor.trackCount) {
            val format = extractor.getTrackFormat(i)
            val mime = format.getString(MediaFormat.KEY_MIME) ?: continue
            if (mime.startsWith("video/") || mime.startsWith("audio/")) {
                extractor.selectTrack(i)
                trackMap[i] = muxer.addTrack(format)
            }
        }

        muxer.start()

        val startUs = segment.startMs * 1000L
        val endUs = segment.endMs * 1000L
        extractor.seekTo(startUs, MediaExtractor.SEEK_TO_CLOSEST_SYNC)

        val buffer = java.nio.ByteBuffer.allocate(CHUNK_SIZE)
        val bufferInfo = android.media.MediaCodec.BufferInfo()

        while (true) {
            val trackIndex = extractor.sampleTrackIndex
            if (trackIndex < 0) break

            val sampleTime = extractor.sampleTime
            if (sampleTime > endUs) break

            val muxerTrack = trackMap[trackIndex] ?: run { extractor.advance(); continue }

            bufferInfo.offset = 0
            bufferInfo.size = extractor.readSampleData(buffer, 0)
            if (bufferInfo.size < 0) break

            bufferInfo.presentationTimeUs = sampleTime - startUs
            bufferInfo.flags = extractor.sampleFlags

            muxer.writeSampleData(muxerTrack, buffer, bufferInfo)
            extractor.advance()
        }

        muxer.stop()
        muxer.release()
        extractor.release()
        Log.d(TAG, "Cut segment [${segment.startMs}ms-${segment.endMs}ms] → ${outputFile.name}")
    }
}

