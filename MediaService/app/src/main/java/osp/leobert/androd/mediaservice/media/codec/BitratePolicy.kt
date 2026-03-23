package osp.leobert.androd.mediaservice.media.codec

import android.util.Log

/**
 * Computes a reasonable HEVC output bitrate for a given target resolution.
 *
 * ## Empirical HEVC bitrate table
 * Values are conservative "good quality" targets for H.265/HEVC at moderate motion.
 * AVC (H.264) content at these bitrates would look roughly equivalent to H.264 at 2×.
 *
 * | Height | Target kbps |
 * |--------|-------------|
 * | 240p   |  400 kbps   |
 * | 360p   |  700 kbps   |
 * | 480p   | 1 200 kbps  |
 * | 720p   | 2 500 kbps  |
 * | 1080p  | 5 000 kbps  |
 * | 1440p  | 8 000 kbps  |
 * | 2160p  |16 000 kbps  |
 *
 * The final bitrate is capped at [inputBitrateKbps] (when > 0) to avoid exceeding the
 * original file's bitrate. An explicit [overrideBitrateKbps] (> 0) further constrains
 * the result — useful when the Python server wants to enforce a specific ceiling.
 */
object BitratePolicy {

    private const val TAG = "BitratePolicy"

    /**
     * Ascending list of (height, HEVC bitrate kbps) pairs.
     * Look-up: largest entry whose height ≤ targetHeight.
     */
    private val TABLE: List<Pair<Int, Int>> = listOf(
         240 to    400,
         360 to    700,
         480 to  1_200,
         720 to  2_500,
        1080 to  5_000,
        1440 to  8_000,
        2160 to 16_000,
    )

    /**
     * @param targetHeight        Output height in pixels (from [ResolutionPolicy]).
     * @param inputBitrateKbps    Probed container bitrate of the input; 0 = unknown.
     * @param overrideBitrateKbps Explicit server-supplied ceiling in kbps; 0 = no override.
     * @return                    Target HEVC bitrate in kbps (always > 0).
     */
    fun computeKbps(
        targetHeight: Int,
        inputBitrateKbps: Int,
        overrideBitrateKbps: Int = 0,
    ): Int {
        // Largest tier whose height ≤ targetHeight, or the lowest tier as floor
        val empirical = TABLE
            .filter { (h, _) -> h <= targetHeight }
            .maxByOrNull { (h, _) -> h }?.second
            ?: TABLE.first().second

        var result = empirical

        // Cap at input bitrate if known (never exceed original quality)
        if (inputBitrateKbps > 0) result = minOf(result, inputBitrateKbps)

        // Apply server override ceiling if set
        if (overrideBitrateKbps > 0) result = minOf(result, overrideBitrateKbps)

        Log.d(TAG, "height=${targetHeight}px empirical=${empirical}kbps " +
            "inputCap=${inputBitrateKbps}kbps override=${overrideBitrateKbps}kbps → ${result}kbps")
        return result
    }
}

