@file:SuppressLint("NewApi") // minSdk=31; MediaMetadataRetriever APIs all require API≤14
package osp.leobert.androd.mediaservice.media.codec

import android.annotation.SuppressLint
import android.media.MediaMetadataRetriever
import android.util.Log
import java.io.File

/**
 * Probes a video file for metadata using [MediaMetadataRetriever].
 *
 * Supports all formats that Android's media framework can demux (MP4, MKV, WebM, etc.).
 * For formats that fail (e.g. some AVI / FLV variants), returns safe fallback values so
 * the pipeline can still proceed — [ResolutionPolicy] and [BitratePolicy] will then use
 * their conservative defaults.
 */
data class VideoProbeResult(
    /** Width in pixels (largest dimension). */
    val widthPx: Int,
    /** Height in pixels (smallest dimension, a.k.a. the "Np" in "720p"). */
    val heightPx: Int,
    /** Total bitrate of the container in kbps (0 if unknown). */
    val bitrateKbps: Int,
    /** Duration in milliseconds. */
    val durationMs: Long,
)

object VideoMetaProber {

    private const val TAG = "VideoMetaProber"

    /** Fallback used when the retriever cannot parse the file. */
    private val FALLBACK = VideoProbeResult(
        widthPx = 1280,
        heightPx = 720,
        bitrateKbps = 0,   // 0 → BitratePolicy will use empirical table without capping
        durationMs = 0L,
    )

    @SuppressLint("NewApi")
    fun probe(file: File): VideoProbeResult {
        val mmr = MediaMetadataRetriever()
        return try {
            mmr.setDataSource(file.absolutePath)

            val rawW = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)?.toIntOrNull() ?: 0
            val rawH = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)?.toIntOrNull() ?: 0
            // Normalize: widthPx = long side, heightPx = short side
            val w = maxOf(rawW, rawH).takeIf { it > 0 } ?: FALLBACK.widthPx
            val h = minOf(rawW, rawH).takeIf { it > 0 } ?: FALLBACK.heightPx

            val bitrateKbps = (
                mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_BITRATE)
                    ?.toLongOrNull() ?: 0L
            ) / 1000L

            val durationMs = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                ?.toLongOrNull() ?: 0L

            VideoProbeResult(w, h, bitrateKbps.toInt(), durationMs).also {
                Log.d(TAG, "Probed ${file.name}: ${it.widthPx}×${it.heightPx}" +
                    " @${it.bitrateKbps}kbps dur=${it.durationMs}ms")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Probe failed for '${file.name}', using fallback: ${e.message}")
            FALLBACK
        } finally {
            runCatching { mmr.release() }
        }
    }
}

