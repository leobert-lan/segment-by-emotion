package osp.leobert.androd.mediaservice.media.pipeline

import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import android.util.Log
import java.io.File

/**
 * Merges a list of cut segment files into a single output file.
 *
 * Strategy: sequential pass-through muxing with monotonic PTS adjustment.
 * Each input file's PTS is offset by the cumulative duration of all preceding clips
 * to prevent timestamp discontinuities in the merged output.
 */
class SegmentMerger {

    companion object {
        private const val TAG = "SegmentMerger"
        private const val CHUNK_SIZE = 1 * 1024 * 1024
    }

    /**
     * @param segmentFiles Ordered list of cut segment .mp4 files
     * @param outputFile   Destination merged .mp4 file
     */
    fun merge(segmentFiles: List<File>, outputFile: File) {
        require(segmentFiles.isNotEmpty()) { "No segments to merge" }

        if (segmentFiles.size == 1) {
            segmentFiles[0].copyTo(outputFile, overwrite = true)
            return
        }

        // Use the first segment to determine track formats for the muxer.
        val firstExtractor = MediaExtractor()
        firstExtractor.setDataSource(segmentFiles[0].absolutePath)
        val muxer = MediaMuxer(outputFile.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)

        data class TrackInfo(val extractorIndex: Int, val muxerIndex: Int, val mime: String)
        val tracks = mutableListOf<TrackInfo>()

        for (i in 0 until firstExtractor.trackCount) {
            val format = firstExtractor.getTrackFormat(i)
            val mime = format.getString(MediaFormat.KEY_MIME) ?: continue
            if (mime.startsWith("video/") || mime.startsWith("audio/")) {
                val muxerIdx = muxer.addTrack(format)
                tracks.add(TrackInfo(i, muxerIdx, mime))
            }
        }
        firstExtractor.release()

        muxer.start()

        var ptsOffsetUs = 0L

        for (segFile in segmentFiles) {
            val extractor = MediaExtractor()
            extractor.setDataSource(segFile.absolutePath)

            // Map extractor track indices to muxer track indices by MIME order
            val trackIndexMap = mutableMapOf<Int, Int>()
            var mimeIdx = 0
            for (i in 0 until extractor.trackCount) {
                val format = extractor.getTrackFormat(i)
                val mime = format.getString(MediaFormat.KEY_MIME) ?: continue
                if (mime.startsWith("video/") || mime.startsWith("audio/")) {
                    if (mimeIdx < tracks.size) {
                        extractor.selectTrack(i)
                        trackIndexMap[i] = tracks[mimeIdx].muxerIndex
                    }
                    mimeIdx++
                }
            }

            val buffer = java.nio.ByteBuffer.allocate(CHUNK_SIZE)
            val info = android.media.MediaCodec.BufferInfo()
            var maxPts = 0L

            while (true) {
                val trackIdx = extractor.sampleTrackIndex
                if (trackIdx < 0) break
                val muxerTrack = trackIndexMap[trackIdx] ?: run { extractor.advance(); continue }

                info.offset = 0
                info.size = extractor.readSampleData(buffer, 0)
                if (info.size < 0) break

                val pts = extractor.sampleTime
                if (pts > maxPts) maxPts = pts
                info.presentationTimeUs = pts + ptsOffsetUs
                info.flags = extractor.sampleFlags

                muxer.writeSampleData(muxerTrack, buffer, info)
                extractor.advance()
            }

            ptsOffsetUs += maxPts + 33_333L // append ~1 frame gap to avoid overlap
            extractor.release()
        }

        muxer.stop()
        muxer.release()
        Log.d(TAG, "Merged ${segmentFiles.size} segments → ${outputFile.name}")
    }
}

