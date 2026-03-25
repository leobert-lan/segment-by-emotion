@file:androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
@file:Suppress("UnsafeOptInUsageError")
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

@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
internal data class ExportPlan(
    val videoMimeType: String,
    val scaleToHeight: Int?,
    val label: String,
)

@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
internal object MediaPipelinePlanner {
    const val MIN_SEGMENT_DURATION_MS = 250L

    fun normalizeInterestingSegments(
        segments: List<VideoSegment>,
        durationMs: Long,
    ): List<VideoSegment> {
        val bounded = segments.asSequence()
            .filter { it.label == VideoSegment.LABEL_INTERESTING }
            .mapNotNull { segment ->
                val boundedStart = segment.startMs.coerceAtLeast(0L)
                val boundedEnd = if (durationMs > 0L) {
                    segment.endMs.coerceIn(0L, durationMs)
                } else {
                    segment.endMs.coerceAtLeast(0L)
                }
                if (boundedEnd <= boundedStart) {
                    null
                } else {
                    VideoSegment(
                        startMs = boundedStart,
                        endMs = boundedEnd,
                        label = VideoSegment.LABEL_INTERESTING,
                    )
                }
            }
            .sortedBy { it.startMs }
            .toList()

        if (bounded.isEmpty()) return emptyList()

        val merged = mutableListOf<VideoSegment>()
        bounded.forEach { segment ->
            val last = merged.lastOrNull()
            if (last != null && segment.startMs <= last.endMs) {
                merged[merged.lastIndex] = last.copy(endMs = maxOf(last.endMs, segment.endMs))
            } else {
                merged += segment
            }
        }
        return merged.filter { it.durationMs >= MIN_SEGMENT_DURATION_MS }
    }

    fun preferredVideoMimeType(codecHint: String): String = when (codecHint.lowercase()) {
        "avc", "h264", MimeTypes.VIDEO_H264 -> MimeTypes.VIDEO_H264
        else -> MimeTypes.VIDEO_H265
    }
}

// videoName is passed in from the task metadata via execute(); kept as a parameter
// so MediaPipeline stays decoupled from VideoMeta/NodeTask.

/**
 * Unified cut → merge → compress pipeline implemented as a **single Media3 Transformer pass**.
 *
 * ## Why a single pass?
 * The legacy SegmentCutter / SegmentMerger used [android.media.MediaExtractor] which does
 * **not** support AVI or FLV containers natively. Media3 Transformer internally uses
 * Media3's extractor stack supports:
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
@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
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

        // ── Probe input metadata ──────────────────────────────────────────
        val probe = withContext(Dispatchers.IO) { VideoMetaProber.probe(inputFile) }
        val interesting = MediaPipelinePlanner.normalizeInterestingSegments(
            segments = params.segments,
            durationMs = probe.durationMs,
        )
        check(interesting.isNotEmpty()) {
            "[$taskId] No valid interesting segments to process " +
                "(received=${params.segments.size}, minDurationMs=${MediaPipelinePlanner.MIN_SEGMENT_DURATION_MS}, " +
                "inputDurationMs=${probe.durationMs})"
        }
        Log.i(
            TAG,
            "[$taskId] Segments after sanitization: ${interesting.size} kept / " +
                "${params.segments.size - interesting.size} skipped",
        )

        val targetHeight = ResolutionPolicy.targetHeight(probe.widthPx, probe.heightPx)
        Log.i(
            TAG, "[$taskId] Input ${probe.widthPx}×${probe.heightPx} @${probe.bitrateKbps}kbps" +
                " → preferred output height=${targetHeight}px"
        )

        onProgress("transcoding", 0f)
        val resultFile = withContext(Dispatchers.IO) { fileStore.resultVideoFile(taskId) }

        val exportPlans = buildExportPlans(
            codecHint = params.codecHint,
            inputHeight = probe.heightPx,
            targetHeight = targetHeight,
        )

        var selectedPlan: ExportPlan? = null
        var selectedBitrateKbps = 0
        var exportFailure: Throwable? = null

        exportPlans.forEachIndexed { index, plan ->
            if (selectedPlan != null) return@forEachIndexed

            val outputHeightForBitrate = plan.scaleToHeight ?: probe.heightPx
            val targetBitrateKbps = BitratePolicy.computeKbps(
                targetHeight = outputHeightForBitrate,
                inputBitrateKbps = probe.bitrateKbps,
                overrideBitrateKbps = params.targetBitrateKbps,
            )

            runCatching {
                if (resultFile.exists()) {
                    resultFile.delete()
                }
                runExportAttempt(
                    taskId = taskId,
                    inputFile = inputFile,
                    resultFile = resultFile,
                    segments = interesting,
                    plan = plan,
                    targetBitrateKbps = targetBitrateKbps,
                    onProgress = onProgress,
                )
            }.onSuccess {
                selectedPlan = plan
                selectedBitrateKbps = targetBitrateKbps
            }.onFailure { failure ->
                exportFailure = failure
                val canRetry = index < exportPlans.lastIndex && shouldRetryExport(failure)
                if (!canRetry) {
                    throw failure
                }
                Log.w(
                    TAG,
                    "[$taskId] Export attempt '${plan.label}' failed; retrying with a safer fallback",
                    failure,
                )
            }
        }

        val finalPlan = selectedPlan ?: throw (exportFailure ?: IllegalStateException("[$taskId] Export failed"))
        val finalTargetHeight = finalPlan.scaleToHeight ?: probe.heightPx
        val mimeType = finalPlan.videoMimeType

        Log.i(TAG, "[$taskId] Pipeline complete → ${resultFile.path}")

        // ── Write result.json ─────────────────────────────────────────────
        withContext(Dispatchers.IO) {
            writeResultJson(
                taskId           = taskId,
                videoName        = videoName,
                params           = params,
                targetHeightPx   = finalTargetHeight,
                targetBitrateKbps = selectedBitrateKbps,
                mimeType         = mimeType,
                resultVideoFile  = resultFile,
                outputFile       = fileStore.resultJsonFile(taskId),
            )
        }

        return resultFile
    }

    private fun buildExportPlans(
        codecHint: String,
        inputHeight: Int,
        targetHeight: Int,
    ): List<ExportPlan> {
        val preferredMime = MediaPipelinePlanner.preferredVideoMimeType(codecHint)
        val fallbackMime = if (preferredMime == MimeTypes.VIDEO_H265) MimeTypes.VIDEO_H264 else MimeTypes.VIDEO_H265
        val availableMimes = listOf(preferredMime, fallbackMime)
            .distinct()
            .filter { mimeType -> runCatching { HardwareCodecSelector.selectEncoder(mimeType) }.isSuccess }
            .ifEmpty { listOf(preferredMime) }

        val scalingHeight = targetHeight.takeIf { it != inputHeight }
        return buildList {
            availableMimes.forEach { mimeType ->
                add(
                    ExportPlan(
                        videoMimeType = mimeType,
                        scaleToHeight = scalingHeight,
                        label = "mime=$mimeType scale=${scalingHeight ?: inputHeight}",
                    )
                )
            }
            if (scalingHeight != null) {
                availableMimes.forEach { mimeType ->
                    add(
                        ExportPlan(
                            videoMimeType = mimeType,
                            scaleToHeight = null,
                            label = "mime=$mimeType native=$inputHeight",
                        )
                    )
                }
            }
        }.distinctBy { it.videoMimeType to it.scaleToHeight }
    }

    @androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
    private suspend fun runExportAttempt(
        taskId: String,
        inputFile: File,
        resultFile: File,
        segments: List<VideoSegment>,
        plan: ExportPlan,
        targetBitrateKbps: Int,
        onProgress: (stage: String, progress: Float) -> Unit,
    ) {
        val inputUri = Uri.fromFile(inputFile)
        val scaleToHeight = plan.scaleToHeight
        val videoEffects: List<Effect> = if (scaleToHeight != null) {
            Log.d(TAG, "[$taskId] Scaling enabled → ${scaleToHeight}px for attempt '${plan.label}'")
            listOf(Presentation.createForHeight(scaleToHeight))
        } else {
            Log.d(TAG, "[$taskId] Scaling disabled for attempt '${plan.label}'")
            emptyList()
        }

        val editedMediaItems = segments.mapIndexed { idx, seg ->
            Log.d(TAG, "[$taskId] Attempt '${plan.label}' clip $idx: [${seg.startMs}–${seg.endMs}ms]")
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
        val composition = Composition.Builder(listOf(sequenceBuilder.build())).build()

        val encoderFactory = DefaultEncoderFactory.Builder(context)
            .setRequestedVideoEncoderSettings(
                VideoEncoderSettings.Builder()
                    .setBitrate(targetBitrateKbps * 1000)
                    .build()
            )
            .build()

        withContext(Dispatchers.Main) {
            val transformer = Transformer.Builder(context)
                .setVideoMimeType(plan.videoMimeType)
                .setAudioMimeType(MimeTypes.AUDIO_AAC)
                .setEncoderFactory(encoderFactory)
                .build()

            val progressHolder = ProgressHolder()
            val mainHandler = Handler(Looper.getMainLooper())

            suspendCancellableCoroutine<Unit> { cont ->
                val pollRunnable = object : Runnable {
                    override fun run() {
                        if (!cont.isActive) return
                        if (transformer.getProgress(progressHolder) == Transformer.PROGRESS_STATE_AVAILABLE) {
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
                            TAG,
                            "[$taskId] Attempt '${plan.label}' done → ${resultFile.name} size=${exportResult.fileSizeBytes}B",
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
                        Log.e(TAG, "[$taskId] Attempt '${plan.label}' failed", exportException)
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
    }

    @androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
    private fun shouldRetryExport(error: Throwable): Boolean {
        var current: Throwable? = error
        while (current != null) {
            val message = current.message.orEmpty()
            if (
                current is ExportException ||
                message.contains("Muxer error", ignoreCase = true) ||
                message.contains("no output sample written", ignoreCase = true) ||
                message.contains("watchdog", ignoreCase = true)
            ) {
                return true
            }
            current = current.cause
        }
        return false
    }
}
