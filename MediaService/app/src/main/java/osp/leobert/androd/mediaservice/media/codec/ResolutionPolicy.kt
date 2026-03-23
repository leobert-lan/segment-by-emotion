package osp.leobert.androd.mediaservice.media.codec

import android.util.Log

/**
 * Determines the output video height based on the input resolution.
 *
 * ## Policy
 * - Output height = the largest **standard tier** that is ≤ the input's short side.
 * - This guarantees we never upscale.
 * - 720p input  → 720p output  (no needless upscale to 1080p).
 * - 1080p input → 1080p output.
 * - 2K (1440p)  → 1440p output (preserved as requested).
 * - 4K (2160p)  → 2160p output (preserved as requested).
 * - Non-standard resolutions (e.g. 960p) round down to the nearest tier (720p).
 * - Input smaller than the lowest tier (240p) is kept at its native height.
 *
 * The corresponding output **width** is left to [androidx.media3.effect.Presentation.createForHeight]
 * which maintains the original aspect ratio automatically.
 */
object ResolutionPolicy {

    private const val TAG = "ResolutionPolicy"

    /** Standard height tiers, ascending. */
    private val HEIGHT_TIERS = listOf(240, 360, 480, 720, 1080, 1440, 2160)

    /**
     * Returns the target output height (short side, in pixels).
     *
     * @param inputWidth  Width of the input video (either orientation is accepted).
     * @param inputHeight Height of the input video (either orientation is accepted).
     */
    fun targetHeight(inputWidth: Int, inputHeight: Int): Int {
        if (inputWidth <= 0 || inputHeight <= 0) {
            Log.w(TAG, "Invalid input dimensions ${inputWidth}×${inputHeight}, defaulting to 720")
            return 720
        }

        // "Height" in standard naming = the short side (e.g. 720p → 720 short side)
        val shortSide = minOf(inputWidth, inputHeight)

        val tier = HEIGHT_TIERS.filter { it <= shortSide }.maxOrNull()
            ?: shortSide // smaller than smallest tier → keep native

        Log.d(TAG, "Input short-side=${shortSide}px → target tier=${tier}px")
        return tier
    }
}

