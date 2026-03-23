package osp.leobert.androd.mediaservice.media.pipeline

import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.media3.common.Effect
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.effect.Presentation
import androidx.media3.transformer.Composition
import androidx.media3.transformer.DefaultEncoderFactory
import androidx.media3.transformer.EditedMediaItem
import androidx.media3.transformer.EditedMediaItemSequence
import androidx.media3.transformer.Effects
import androidx.media3.transformer.ExportException
import androidx.media3.transformer.ExportResult
import androidx.media3.transformer.ProgressHolder
import androidx.media3.transformer.Transformer
import androidx.media3.transformer.VideoEncoderSettings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import osp.leobert.androd.mediaservice.domain.model.ProcessingParams
import osp.leobert.androd.mediaservice.domain.model.VideoSegment
import osp.leobert.androd.mediaservice.media.codec.BitratePolicy
import osp.leobert.androd.mediaservice.media.codec.HardwareCodecSelector
import osp.leobert.androd.mediaservice.media.codec.ResolutionPolicy
import osp.leobert.androd.mediaservice.media.codec.VideoMetaProber
import osp.leobert.androd.mediaservice.storage.file.FileStoreManager
import java.io.File
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

// videoName is passed in from the task metadata via execute(); kept as a parameter
// so MediaPipeline stays decoupled from VideoMeta/NodeTask.

/**
 * Unified cut → merge → compress pipeline implemented as a **single Media3 Transformer pass**.
 *
 * ## Why a single pass?
 * The legacy SegmentCutter / SegmentMerger used [android.media.MediaExtractor] which does
 * **not** support AVI or FLV containers natively. Media3 Transformer internally uses
 * ExoPlayer's [androidx.media3.exoplayer.DefaultExtractorsFactory] which supports:
 *   MP4, MKV, WebM, AVI, FLV, MPEG-TS, Ogg, and more.
 *
 * All three stages (clip, concatenate, encode) are handled by a single
 * [Composition] of [EditedMediaItem]s with [MediaItem.ClippingConfiguration],
 * using hardware HEVC encoding via [HardwareCodecSelector].
 *
 * ## Segment filtering
 * Only segments with [VideoSegment.LABEL_INTERESTING] are included.
 * "uninteresting" and "unlabeled" segments are silently skipped.
 *
 * ## Resolution policy
 * Output height = largest standard tier ≤ input height (never upscales).
 * See [ResolutionPolicy] for tier table.
 *
 * ## Bitrate policy
 * Empirical HEVC bitrate per tier, capped at the input file's actual bitrate.
 * See [BitratePolicy] for the table.
 */
class MediaPipeline(
    private val context: Context,
    private val fileStore: FileStoreManager,
) {

    companion object {
        private const val TAG = "MediaPipeline"
        /** Progress polling interval (ms) — Transformer progress is approximate anyway. */
        private const val PROGRESS_POLL_MS = 500L
    }

    /**
     * Execute the full pipeline.
     *
     * @param taskId     Task identifier (used for directory layout)
     * @param videoName  Original video file name (written into result.json)
     * @param params     Processing parameters from the Python server
     * @param onProgress (stage: String, progress: Float) — stage is "transcoding"
     * @return           The output video [File]
     */
    suspend fun execute(
        taskId: String,
        videoName: String,
        params: ProcessingParams,
        onProgress: (stage: String, progress: Float) -> Unit,
    ): File {
        val inputFile = withContext(Dispatchers.IO) { fileStore.assembledFile(taskId) }
        check(inputFile.exists()) { "Assembled input file not found: ${inputFile.path}" }

        // ── Filter: interesting segments only ─────────────────────────────
        val interesting = params.segments.filter {
            it.label == VideoSegment.LABEL_INTERESTING
        }
        check(interesting.isNotEmpty()) {
            "[$taskId] No interesting segments to process " +
                "(total received: ${params.segments.size})"
        }
        Log.i(
            TAG, "[$taskId] Segments: ${interesting.size} interesting / " +
                "${params.segments.size - interesting.size} skipped"
        )

        // ── Probe input metadata ──────────────────────────────────────────
        val probe = withContext(Dispatchers.IO) { VideoMetaProber.probe(inputFile) }
        val targetHeight = ResolutionPolicy.targetHeight(probe.widthPx, probe.heightPx)
        val targetBitrateKbps = BitratePolicy.computeKbps(
            targetHeight       = targetHeight,
            inputBitrateKbps   = probe.bitrateKbps,
            overrideBitrateKbps = params.targetBitrateKbps,
        )
        Log.i(
            TAG, "[$taskId] Input ${probe.widthPx}×${probe.heightPx} @${probe.bitrateKbps}kbps" +
                " → output height=${targetHeight}px @${targetBitrateKbps}kbps"
        )

        onProgress("transcoding", 0f)
        val resultFile = withContext(Dispatchers.IO) { fileStore.resultVideoFile(taskId) }

        // ── Build Composition ─────────────────────────────────────────────
        val inputUri = Uri.fromFile(inputFile)

        val hevcChoice = runCatching {
            HardwareCodecSelector.selectEncoder(MimeTypes.VIDEO_H265)
        }.getOrNull()
        val mimeType = if (hevcChoice != null) MimeTypes.VIDEO_H265 else MimeTypes.VIDEO_H264
        Log.i(TAG, "[$taskId] Encoder mime=$mimeType hw=${hevcChoice?.isHardware}")

        // Presentation effect scales each clip to targetHeight (aspect ratio preserved).
        // Skip if input is already at the target height — avoids unnecessary GPU scaling
        // and may allow Transformer to skip the GL pipeline entirely.
        val needsScaling = probe.heightPx != targetHeight
        val videoEffects: List<Effect> = if (needsScaling) {
            Log.d(TAG, "[$taskId] Scaling ${probe.heightPx}px → ${targetHeight}px (Presentation effect)")
            listOf(Presentation.createForHeight(targetHeight))
        } else {
            Log.d(TAG, "[$taskId] No scaling needed (input already at ${targetHeight}px)")
            emptyList()
        }

        val editedMediaItems = interesting.mapIndexed { idx, seg ->
            Log.d(TAG, "[$taskId] Clip $idx: [${seg.startMs}–${seg.endMs}ms]")
            EditedMediaItem.Builder(
                MediaItem.Builder()
                    .setUri(inputUri)
                    .setClippingConfiguration(
                        MediaItem.ClippingConfiguration.Builder()
                            .setStartPositionMs(seg.startMs)
                            .setEndPositionMs(seg.endMs)
                            .build()
                    )
                    .build()
            )
                .setEffects(Effects(emptyList(), videoEffects))
                .build()
        }

        val sequenceBuilder = EditedMediaItemSequence.Builder()
        editedMediaItems.forEach { sequenceBuilder.addItem(it) }

        val composition = Composition.Builder(
            listOf(sequenceBuilder.build())
        ).build()

        val encoderFactory = DefaultEncoderFactory.Builder(context)
            .setRequestedVideoEncoderSettings(
                VideoEncoderSettings.Builder()
                    .setBitrate(targetBitrateKbps * 1000) // kbps → bps
                    .build()
            )
            .build()

        // ── Run Transformer on the main thread ────────────────────────────
        // Media3 Transformer.start() verifies it is called from the thread that
        // built the Transformer (applicationLooper). Building on main avoids the
        // IllegalStateException thrown when called from a looper-less IO thread.
        //
        // Audio notes:
        //   • Audio tracks ARE included (no setRemoveAudio() call).
        //   • ClippingConfiguration clips audio and video together for each segment.
        //   • setAudioMimeType(AAC) forces re-encode to AAC, which is required for
        //     multi-segment PTS concatenation (each segment's audio PTS starts from 0;
        //     the sequence must stitch them continuously).
        //   • Without explicit AAC, inputs with AC3/DTS/EAC3 (common in MKV/AVI)
        //     may hit unsupported-codec paths on some devices.
        withContext(Dispatchers.Main) {
            val transformer = Transformer.Builder(context)
                .setVideoMimeType(mimeType)
                .setAudioMimeType(MimeTypes.AUDIO_AAC) // AAC: universally supported; required for multi-segment PTS stitching
                .setEncoderFactory(encoderFactory)
                .build()

            val progressHolder = ProgressHolder()
            val mainHandler = Handler(Looper.getMainLooper())

            suspendCancellableCoroutine<Unit> { cont ->
                // Progress polling — getProgress() must be called on the main thread
                val pollRunnable = object : Runnable {
                    override fun run() {
                        if (!cont.isActive) return
                        if (transformer.getProgress(progressHolder) ==
                            Transformer.PROGRESS_STATE_AVAILABLE
                        ) {
                            onProgress("transcoding", progressHolder.progress / 100f)
                        }
                        mainHandler.postDelayed(this, PROGRESS_POLL_MS)
                    }
                }
                mainHandler.postDelayed(pollRunnable, PROGRESS_POLL_MS)

                transformer.addListener(object : Transformer.Listener {
                    override fun onCompleted(
                        composition: Composition,
                        exportResult: ExportResult,
                    ) {
                        mainHandler.removeCallbacks(pollRunnable)
                        Log.i(
                            TAG, "[$taskId] Transcoding done → ${resultFile.name}" +
                                " size=${exportResult.fileSizeBytes}B"
                        )
                        onProgress("transcoding", 1f)
                        cont.resume(Unit)
                    }

                    override fun onError(
                        composition: Composition,
                        exportResult: ExportResult,
                        exportException: ExportException,
                    ) {
                        mainHandler.removeCallbacks(pollRunnable)
                        Log.e(TAG, "[$taskId] Transcoding error", exportException)
                        cont.resumeWithException(exportException)
                    }
                })

                transformer.start(composition, resultFile.absolutePath)

                cont.invokeOnCancellation {
                    mainHandler.removeCallbacks(pollRunnable)
                    transformer.cancel()
                }
            }
        }

        Log.i(TAG, "[$taskId] Pipeline complete → ${resultFile.path}")

        // ── Write result.json ─────────────────────────────────────────────
        withContext(Dispatchers.IO) {
            writeResultJson(
                taskId           = taskId,
                videoName        = videoName,
                params           = params,
                targetHeightPx   = targetHeight,
                targetBitrateKbps = targetBitrateKbps,
                mimeType         = mimeType,
                resultVideoFile  = resultFile,
                outputFile       = fileStore.resultJsonFile(taskId),
            )
        }

        return resultFile
    }
}
