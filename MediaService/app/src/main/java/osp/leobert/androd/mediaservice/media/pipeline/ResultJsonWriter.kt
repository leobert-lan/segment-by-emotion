package osp.leobert.androd.mediaservice.media.pipeline

import android.util.Log
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import osp.leobert.androd.mediaservice.domain.model.ProcessingParams
import osp.leobert.androd.mediaservice.domain.model.VideoSegment
import java.io.File

// ── Wire-format data classes (schema_version = "v1") ─────────────────────────

@Serializable
private data class ResultJson(
    val schema_version: String = "v1",
    val task: TaskInfo,
    val summary: Summary,
    val processed_segments: List<SegmentInfo>,
    val output: OutputInfo,
    val processed_at_ms: Long,
)

@Serializable
private data class TaskInfo(
    val id: String,
    val video_name: String,
    val status: String = "done",
)

@Serializable
private data class Summary(
    /** Total number of segments received from the Python server. */
    val total_segments_received: Int,
    val interesting_count: Int,
    val uninteresting_count: Int,
    val unlabeled_count: Int,
    /** Segments that were actually transcoded (= interesting_count). */
    val processed_count: Int,
)

@Serializable
private data class SegmentInfo(
    val start_ms: Long,
    val end_ms: Long,
    @SerialName("start_sec") val startSec: Double,
    @SerialName("end_sec") val endSec: Double,
    val label: String,
)

@Serializable
private data class OutputInfo(
    val file_name: String,
    val file_size_bytes: Long,
    val target_height_px: Int,
    val target_bitrate_kbps: Int,
    val mime_type: String,
)

// ── Writer ────────────────────────────────────────────────────────────────────

private val jsonEncoder = Json { prettyPrint = true; encodeDefaults = true }

private const val TAG = "ResultJsonWriter"

/**
 * Generates and writes `result.json` to [outputFile] after the pipeline completes.
 *
 * The JSON schema is v1 and matches the Android processing summary:
 * - task / summary / processed_segments / output
 * - `label_events` is omitted (those are Python-side data).
 *
 * @param taskId           Task identifier.
 * @param videoName        Original video file name from [VideoMeta].
 * @param params           Full [ProcessingParams] (all segments, including skipped ones).
 * @param targetHeightPx   Output resolution height from [ResolutionPolicy].
 * @param targetBitrateKbps Output bitrate from [BitratePolicy].
 * @param mimeType         Output video MIME type (e.g. "video/hevc").
 * @param resultVideoFile  The transcoded output file (used to read file size).
 * @param outputFile       Destination `result.json` file path.
 */
fun writeResultJson(
    taskId: String,
    videoName: String,
    params: ProcessingParams,
    targetHeightPx: Int,
    targetBitrateKbps: Int,
    mimeType: String,
    resultVideoFile: File,
    outputFile: File,
) {
    val interesting  = params.segments.filter { it.label == VideoSegment.LABEL_INTERESTING }
    val uninteresting = params.segments.filter { it.label == VideoSegment.LABEL_UNINTERESTING }
    val unlabeled    = params.segments.filter { it.label == VideoSegment.LABEL_UNLABELED }

    val result = ResultJson(
        task = TaskInfo(id = taskId, video_name = videoName),
        summary = Summary(
            total_segments_received = params.segments.size,
            interesting_count  = interesting.size,
            uninteresting_count = uninteresting.size,
            unlabeled_count    = unlabeled.size,
            processed_count    = interesting.size,
        ),
        processed_segments = interesting.map { seg ->
            SegmentInfo(
                start_ms = seg.startMs,
                end_ms   = seg.endMs,
                startSec = seg.startMs / 1000.0,
                endSec   = seg.endMs   / 1000.0,
                label    = seg.label,
            )
        },
        output = OutputInfo(
            file_name          = resultVideoFile.name,
            file_size_bytes    = resultVideoFile.length(),
            target_height_px   = targetHeightPx,
            target_bitrate_kbps = targetBitrateKbps,
            mime_type          = mimeType,
        ),
        processed_at_ms = System.currentTimeMillis(),
    )

    runCatching {
        outputFile.writeText(jsonEncoder.encodeToString(result))
        Log.i(TAG, "[$taskId] result.json written → ${outputFile.name} (${outputFile.length()}B)")
    }.onFailure { e ->
        Log.e(TAG, "[$taskId] Failed to write result.json", e)
    }
}

