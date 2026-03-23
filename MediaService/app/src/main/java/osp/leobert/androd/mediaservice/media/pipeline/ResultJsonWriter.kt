package osp.leobert.androd.mediaservice.media.pipeline

import android.util.Log
import com.google.gson.GsonBuilder
import com.google.gson.annotations.SerializedName
import osp.leobert.androd.mediaservice.domain.model.ProcessingParams
import osp.leobert.androd.mediaservice.domain.model.VideoSegment
import java.io.File

// ── Wire-format data classes (schema_version = "v1") ─────────────────────────

private data class ResultJson(
    @SerializedName("schema_version") val schemaVersion: String = "v1",
    @SerializedName("task") val task: TaskInfo,
    @SerializedName("summary") val summary: Summary,
    @SerializedName("processed_segments") val processedSegments: List<SegmentInfo>,
    @SerializedName("output") val output: OutputInfo,
    @SerializedName("processed_at_ms") val processedAtMs: Long,
)

private data class TaskInfo(
    @SerializedName("id") val id: String,
    @SerializedName("video_name") val videoName: String,
    @SerializedName("status") val status: String = "done",
)

private data class Summary(
    /** Total number of segments received from the Python server. */
    @SerializedName("total_segments_received") val totalSegmentsReceived: Int,
    @SerializedName("interesting_count") val interestingCount: Int,
    @SerializedName("uninteresting_count") val uninterestingCount: Int,
    @SerializedName("unlabeled_count") val unlabeledCount: Int,
    /** Segments that were actually transcoded (= interesting_count). */
    @SerializedName("processed_count") val processedCount: Int,
)

private data class SegmentInfo(
    @SerializedName("start_ms") val startMs: Long,
    @SerializedName("end_ms") val endMs: Long,
    @SerializedName("start_sec") val startSec: Double,
    @SerializedName("end_sec") val endSec: Double,
    @SerializedName("label") val label: String,
)

private data class OutputInfo(
    @SerializedName("file_name") val fileName: String,
    @SerializedName("file_size_bytes") val fileSizeBytes: Long,
    @SerializedName("target_height_px") val targetHeightPx: Int,
    @SerializedName("target_bitrate_kbps") val targetBitrateKbps: Int,
    @SerializedName("mime_type") val mimeType: String,
)

// ── Writer ────────────────────────────────────────────────────────────────────

private val jsonEncoder = GsonBuilder().setPrettyPrinting().create()

private const val TAG = "ResultJsonWriter"

/**
 * Generates and writes `result.json` to [outputFile] after the pipeline completes.
 *
 * The JSON schema is v1 and matches the Android processing summary:
 * - task / summary / processed_segments / output
 * - `label_events` is omitted (those are Python-side data).
 *
 * @param taskId           Task identifier.
 * @param videoName        Original video file name from task video metadata.
 * @param params           Full [ProcessingParams] (all segments, including skipped ones).
 * @param targetHeightPx   Output resolution height chosen by pipeline policy.
 * @param targetBitrateKbps Output bitrate chosen by pipeline policy.
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
        task = TaskInfo(id = taskId, videoName = videoName),
        summary = Summary(
            totalSegmentsReceived = params.segments.size,
            interestingCount  = interesting.size,
            uninterestingCount = uninteresting.size,
            unlabeledCount    = unlabeled.size,
            processedCount    = interesting.size,
        ),
        processedSegments = interesting.map { seg ->
            SegmentInfo(
                startMs = seg.startMs,
                endMs   = seg.endMs,
                startSec = seg.startMs / 1000.0,
                endSec   = seg.endMs   / 1000.0,
                label    = seg.label,
            )
        },
        output = OutputInfo(
            fileName          = resultVideoFile.name,
            fileSizeBytes    = resultVideoFile.length(),
            targetHeightPx   = targetHeightPx,
            targetBitrateKbps = targetBitrateKbps,
            mimeType          = mimeType,
        ),
        processedAtMs = System.currentTimeMillis(),
    )

    runCatching {
        outputFile.writeText(jsonEncoder.toJson(result))
        Log.i(TAG, "[$taskId] result.json written → ${outputFile.name} (${outputFile.length()}B)")
    }.onFailure { e ->
        Log.e(TAG, "[$taskId] Failed to write result.json", e)
    }
}

