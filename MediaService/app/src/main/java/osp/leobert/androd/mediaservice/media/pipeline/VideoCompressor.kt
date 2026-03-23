package osp.leobert.androd.mediaservice.media.pipeline

import android.content.Context
import android.util.Log
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.transformer.Composition
import androidx.media3.transformer.DefaultEncoderFactory
import androidx.media3.transformer.EditedMediaItem
import androidx.media3.transformer.EditedMediaItemSequence
import androidx.media3.transformer.ExportException
import androidx.media3.transformer.ExportResult
import androidx.media3.transformer.Transformer
import com.google.common.collect.ImmutableList
import kotlinx.coroutines.suspendCancellableCoroutine
import osp.leobert.androd.mediaservice.media.codec.HardwareCodecSelector
import java.io.File
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * Compresses [inputFile] to [outputFile] using Media3 Transformer.
 *
 * Encoder selection: HardwareCodecSelector → Qualcomm HEVC HW → AVC HW → SW fallback.
 * Target MIME: HEVC ("video/hevc") by default; falls back to AVC if HEVC unavailable.
 *
 * The Transformer API runs asynchronously; this class wraps it as a suspend function
 * via [suspendCancellableCoroutine].
 *
 * **Note**: [MediaPipeline] now performs clip + merge + encode in a single Transformer
 * [Composition] pass with auto-detected resolution and bitrate policies. This class is
 * kept as a standalone utility for cases where only a single-file re-encode is needed.
 */
class VideoCompressor(private val context: Context) {

    companion object {
        private const val TAG = "VideoCompressor"
        private const val DEFAULT_BITRATE_KBPS = 2000
    }

    /**
     * @param inputFile         Merged source video
     * @param outputFile        Compressed output destination
     * @param targetBitrateKbps Target video bitrate (kbps)
     * @param onProgress        Progress callback [0.0, 1.0] — called periodically from the
     *                          Transformer listener thread
     */
    suspend fun compress(
        inputFile: File,
        outputFile: File,
        targetBitrateKbps: Int = DEFAULT_BITRATE_KBPS,
        onProgress: ((Float) -> Unit)? = null,
    ) {
        // Select best available encoder; prefer HEVC for Snapdragon 880.
        val hevcChoice = runCatching { HardwareCodecSelector.selectEncoder(MimeTypes.VIDEO_H265) }.getOrNull()
        val mimeType = if (hevcChoice != null) MimeTypes.VIDEO_H265 else MimeTypes.VIDEO_H264

        Log.i(TAG, "Compressing with mime=$mimeType, bitrate=${targetBitrateKbps}kbps")

        val editedItem = EditedMediaItem.Builder(
            MediaItem.fromUri(android.net.Uri.fromFile(inputFile))
        ).build()

        val transformer = Transformer.Builder(context)
            .setVideoMimeType(mimeType)
            .build()

        suspendCancellableCoroutine { cont ->
            transformer.addListener(object : Transformer.Listener {
                override fun onCompleted(composition: Composition, exportResult: ExportResult) {
                    Log.d(TAG, "Compression complete: ${outputFile.name}")
                    cont.resume(Unit)
                }

                override fun onError(
                    composition: Composition,
                    exportResult: ExportResult,
                    exportException: ExportException,
                ) {
                    Log.e(TAG, "Compression error", exportException)
                    cont.resumeWithException(exportException)
                }
            })

            transformer.start(editedItem, outputFile.absolutePath)

            cont.invokeOnCancellation { transformer.cancel() }
        }
    }
}

