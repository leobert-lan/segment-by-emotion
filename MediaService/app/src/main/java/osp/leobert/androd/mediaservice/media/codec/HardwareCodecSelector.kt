package osp.leobert.androd.mediaservice.media.codec

import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaFormat
import android.util.Log

/**
 * Selects the best available hardware video encoder for the given MIME type.
 *
 * Priority on Snapdragon 880:
 *   1. c2.qti.hevc.encoder   (HEVC hardware, Hexagon 780 DSP)
 *   2. c2.qti.avc.encoder    (H.264 hardware, Adreno 660)
 *   3. Any non-Google hardware encoder for the MIME type
 *   4. Software fallback (OMX.google.*)
 *
 * MIME constants: "video/hevc" (H.265), "video/avc" (H.264)
 */
object HardwareCodecSelector {

    private const val TAG = "HardwareCodecSelector"

    /** Qualcomm codec name prefixes — used to identify HW codecs on Snapdragon. */
    private val QUALCOMM_PREFIXES = listOf("c2.qti.", "c2.qcom.", "OMX.qcom.")
    private val SW_PREFIXES = listOf("OMX.google.", "c2.android.")

    data class CodecChoice(
        val codecName: String,
        val isHardware: Boolean,
        val mimeType: String,
    )

    /**
     * Returns the best encoder [CodecChoice] for [mimeType].
     * Never returns null; falls back to system-selected encoder as last resort.
     */
    fun selectEncoder(mimeType: String): CodecChoice {
        val codecList = MediaCodecList(MediaCodecList.REGULAR_CODECS)
        val allCodecs = codecList.codecInfos
        val encoders = allCodecs.filter { info ->
            info.isEncoder && info.supportedTypes.any { it.equals(mimeType, ignoreCase = true) }
        }

        // 1. Prefer Qualcomm-named hardware encoders
        val qualcomm = encoders.firstOrNull { info ->
            QUALCOMM_PREFIXES.any { info.name.startsWith(it) }
        }
        if (qualcomm != null) {
            Log.i(TAG, "Selected Qualcomm HW encoder: ${qualcomm.name} for $mimeType")
            return CodecChoice(qualcomm.name, isHardware = true, mimeType)
        }

        // 2. Any non-SW encoder
        val hwEncoder = encoders.firstOrNull { info ->
            SW_PREFIXES.none { info.name.startsWith(it) }
        }
        if (hwEncoder != null) {
            Log.i(TAG, "Selected HW encoder: ${hwEncoder.name} for $mimeType")
            return CodecChoice(hwEncoder.name, isHardware = true, mimeType)
        }

        // 3. Software fallback
        val swEncoder = encoders.firstOrNull()
        if (swEncoder != null) {
            Log.w(TAG, "Falling back to SW encoder: ${swEncoder.name} for $mimeType")
            return CodecChoice(swEncoder.name, isHardware = false, mimeType)
        }

        // 4. Let MediaCodecList decide (should never reach here)
        val fallbackName = codecList.findEncoderForFormat(
            MediaFormat.createVideoFormat(mimeType, 1920, 1080)
        ) ?: error("No encoder found for $mimeType")
        Log.w(TAG, "Using system-selected encoder: $fallbackName for $mimeType")
        return CodecChoice(fallbackName, isHardware = false, mimeType)
    }

    /**
     * Logs all available video encoders — useful for device capability auditing.
     */
    fun logAvailableEncoders() {
        val all = MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos
        all.filter { it.isEncoder && it.supportedTypes.any { t -> t.startsWith("video/") } }
            .forEach { Log.d(TAG, "Encoder: ${it.name} → ${it.supportedTypes.toList()}") }
    }
}
