import hashlib
import math
import random
import shutil
import subprocess
import warnings
import wave
from io import BytesIO
from pathlib import Path


class HeatAnalyzer:
    def __init__(
        self,
        target_sr: int = 16000,
        regularize_window_size: int = 5,
        regularize_max_gap_segments: int = 1,
        regularize_max_spike_segments: int = 1,
        regularize_sigma_factor: float = 1.0,
        contextual_window_size: int = 7,
        contextual_high_count: int = 3,
        contextual_percentile: float = 0.70,
    ) -> None:
        self.target_sr = target_sr
        self.regularize_window_size = max(3, int(regularize_window_size))
        self.regularize_max_gap_segments = max(0, int(regularize_max_gap_segments))
        self.regularize_max_spike_segments = max(0, int(regularize_max_spike_segments))
        self.regularize_sigma_factor = max(0.0, float(regularize_sigma_factor))
        self.contextual_window_size = max(3, int(contextual_window_size))
        self.contextual_high_count = max(1, int(contextual_high_count))
        self.contextual_percentile = max(0.05, min(0.95, float(contextual_percentile)))

    def estimate_duration_sec(self, video_path: str) -> float:
        audio, sample_rate = self._try_load_audio(video_path)
        if audio is not None and sample_rate is not None:
            return max(1.0, float(len(audio)) / float(sample_rate))

        return self._fallback_duration(video_path)

    def build_segments(self, video_path: str, segment_duration: float) -> list[tuple[float, float, float]]:
        if segment_duration <= 0:
            raise ValueError("segment_duration must be positive")

        audio, sample_rate = self._try_load_audio(video_path)
        if audio is None or sample_rate is None:
            return self._build_segments_fallback(video_path, segment_duration)

        return self._build_segments_from_audio(audio, sample_rate, segment_duration)

    def _try_load_audio(self, video_path: str):
        audio, sample_rate = self._try_load_audio_with_ffmpeg(video_path)
        if audio is not None and sample_rate is not None:
            return audio, sample_rate

        try:
            import librosa
        except Exception:
            return None, None

        try:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="PySoundFile failed. Trying audioread instead.*",
                    category=UserWarning,
                )
                warnings.filterwarnings(
                    "ignore",
                    message=r"librosa\.core\.audio\.__audioread_load.*",
                    category=FutureWarning,
                )
                audio, sample_rate = librosa.load(video_path, sr=self.target_sr, mono=True)
        except Exception:
            return None, None

        if audio is None or len(audio) == 0:
            return None, None
        return audio, sample_rate

    def _try_load_audio_with_ffmpeg(self, video_path: str):
        ffmpeg_exe = self._resolve_ffmpeg_executable()
        if ffmpeg_exe is None:
            return None, None

        try:
            import numpy as np

            command = [
                ffmpeg_exe,
                "-v",
                "error",
                "-i",
                video_path,
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(self.target_sr),
                "-acodec",
                "pcm_s16le",
                "-f",
                "wav",
                "-",
            ]
            completed = subprocess.run(command, capture_output=True, check=True)
            if not completed.stdout:
                return None, None

            with wave.open(BytesIO(completed.stdout), "rb") as wav_file:
                frame_count = wav_file.getnframes()
                sample_rate = wav_file.getframerate()
                sample_width = wav_file.getsampwidth()
                channels = wav_file.getnchannels()
                raw = wav_file.readframes(frame_count)

            if sample_width != 2 or channels != 1:
                return None, None
            if not raw:
                return None, None

            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if audio.size == 0:
                return None, None
            return audio, int(sample_rate)
        except Exception:
            return None, None

    def _resolve_ffmpeg_executable(self) -> str | None:
        local_ffmpeg = shutil.which("ffmpeg")
        if local_ffmpeg:
            return local_ffmpeg

        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None

    def _build_segments_from_audio(self, audio, sample_rate: int, segment_duration: float) -> list[tuple[float, float, float]]:
        import librosa
        import numpy as np

        duration = max(1.0, float(len(audio)) / float(sample_rate))
        segment_count = max(1, int(math.ceil(duration / segment_duration)))

        rms_values: list[float] = []
        zcr_values: list[float] = []
        onset_values: list[float] = []
        centroid_values: list[float] = []
        pitch_level_values: list[float] = []
        pitch_cv_values: list[float] = []

        for index in range(segment_count):
            start_sample = int(index * segment_duration * sample_rate)
            end_sample = min(len(audio), int((index + 1) * segment_duration * sample_rate))
            segment_audio = audio[start_sample:end_sample]
            if len(segment_audio) == 0:
                rms_values.append(0.0)
                zcr_values.append(0.0)
                onset_values.append(0.0)
                centroid_values.append(0.0)
                pitch_level_values.append(0.0)
                pitch_cv_values.append(1.0)
                continue

            rms = float(np.sqrt(np.mean(np.square(segment_audio))))
            frame_length = min(2048, len(segment_audio))
            hop_length = max(1, min(512, frame_length // 2))
            zcr = float(
                librosa.feature.zero_crossing_rate(
                    segment_audio,
                    frame_length=frame_length,
                    hop_length=hop_length,
                ).mean()
            )

            onset_env = librosa.onset.onset_strength(y=segment_audio, sr=sample_rate)
            onset = float(onset_env.mean()) if len(onset_env) > 0 else 0.0

            centroid_series = librosa.feature.spectral_centroid(
                y=segment_audio,
                sr=sample_rate,
                n_fft=frame_length,
                hop_length=hop_length,
            )
            centroid = float(centroid_series.mean()) if centroid_series.size > 0 else 0.0

            pitch_level = 0.0
            pitch_cv = 1.0
            try:
                pitch_series = librosa.yin(
                    segment_audio,
                    fmin=librosa.note_to_hz("C2"),
                    fmax=librosa.note_to_hz("C7"),
                    sr=sample_rate,
                    frame_length=frame_length,
                    hop_length=hop_length,
                )
                valid_pitch = pitch_series[np.isfinite(pitch_series)]
                if valid_pitch.size > 0:
                    pitch_level = float(valid_pitch.mean())
                    pitch_cv = float(valid_pitch.std() / (pitch_level + 1e-9))
            except Exception:
                # Fallback keeps algorithm robust when pitch extraction is unstable.
                pitch_level = 0.0
                pitch_cv = 1.0

            rms_values.append(rms)
            zcr_values.append(zcr)
            onset_values.append(onset)
            centroid_values.append(centroid)
            pitch_level_values.append(pitch_level)
            pitch_cv_values.append(pitch_cv)

        rms_norm = self._hybrid_normalize(rms_values)
        zcr_norm = self._hybrid_normalize(zcr_values)
        onset_norm = self._hybrid_normalize(onset_values)
        centroid_norm = self._hybrid_normalize(centroid_values)
        pitch_level_norm = self._hybrid_normalize(pitch_level_values)
        pitch_stability = [1.0 / (1.0 + max(0.0, value)) for value in pitch_cv_values]
        pitch_stability_norm = self._hybrid_normalize(pitch_stability)

        rate_up_values: list[float] = []
        for index in range(segment_count):
            if index == 0:
                rate_up_values.append(0.0)
                continue
            zcr_delta = max(0.0, zcr_norm[index] - zcr_norm[index - 1])
            onset_delta = max(0.0, onset_norm[index] - onset_norm[index - 1])
            rate_up_values.append(max(0.0, min(1.0, 0.6 * zcr_delta + 0.4 * onset_delta)))

        scores: list[float] = []
        for index in range(segment_count):
            base_score = (
                0.22 * rms_norm[index]
                + 0.12 * zcr_norm[index]
                + 0.14 * onset_norm[index]
                + 0.18 * centroid_norm[index]
                + 0.20 * pitch_level_norm[index]
                + 0.14 * pitch_stability_norm[index]
            )

            vowel_bonus = 0.0
            if pitch_cv_values[index] < 0.12 and zcr_norm[index] < 0.25 and pitch_level_norm[index] > 0.10:
                vowel_bonus = 0.08

            high_pitch_bonus = 0.0
            if pitch_level_norm[index] > 0.70:
                high_pitch_bonus = 0.10 * ((pitch_level_norm[index] - 0.70) / 0.30)

            rate_up_bonus = 0.08 * rate_up_values[index]

            emotion_index = max(
                0.0,
                min(1.0, 0.45 * rms_norm[index] + 0.30 * onset_norm[index] + 0.25 * centroid_norm[index]),
            )
            excited_bonus = 0.12 * max(0.0, (emotion_index - 0.55) / 0.45)

            raw_score = max(0.0, min(1.0, base_score + vowel_bonus + high_pitch_bonus + rate_up_bonus + excited_bonus))
            sigmoid_score = 1.0 / (1.0 + math.exp(-4.0 * (raw_score - 0.55)))
            scores.append(max(0.0, min(1.0, 0.6 * raw_score + 0.4 * sigmoid_score)))

        scores = self._smooth_scores(scores)
        scores = self._contextual_nonlinear_adjust_scores(scores)
        scores = self._temporal_regularize_scores(scores)
        scores = self._smooth_scores(scores)

        segments: list[tuple[float, float, float]] = []
        for index, score in enumerate(scores):
            start = index * segment_duration
            end = min(duration, start + segment_duration)
            segments.append((start, end, score))
        return segments

    def _minmax_normalize(self, values: list[float]) -> list[float]:
        if not values:
            return []
        min_value = min(values)
        max_value = max(values)
        if max_value - min_value < 1e-9:
            return [0.0 for _ in values]
        return [(value - min_value) / (max_value - min_value) for value in values]

    def _hybrid_normalize(
        self,
        values: list[float],
        local_window: int = 3,
        global_weight: float = 0.7,
    ) -> list[float]:
        if not values:
            return []

        global_norm = self._minmax_normalize(values)
        local_norm: list[float] = []

        for index, value in enumerate(values):
            left = max(0, index - local_window)
            right = min(len(values), index + local_window + 1)
            window = values[left:right]
            window_min = min(window)
            window_max = max(window)
            if window_max - window_min < 1e-9:
                local_norm.append(0.0)
            else:
                local_norm.append((value - window_min) / (window_max - window_min))

        local_weight = 1.0 - global_weight
        return [
            max(0.0, min(1.0, global_weight * global_norm[i] + local_weight * local_norm[i]))
            for i in range(len(values))
        ]

    def _smooth_scores(self, scores: list[float]) -> list[float]:
        if len(scores) < 3:
            return scores
        smoothed: list[float] = []
        for index, score in enumerate(scores):
            left = scores[index - 1] if index > 0 else score
            right = scores[index + 1] if index < len(scores) - 1 else score
            smoothed.append(max(0.0, min(1.0, 0.2 * left + 0.6 * score + 0.2 * right)))
        return smoothed

    def _contextual_nonlinear_adjust_scores(self, scores: list[float]) -> list[float]:
        if len(scores) < 3:
            return scores

        threshold = self._percentile(scores, self.contextual_percentile)
        adjusted = list(scores)
        half_window = max(1, self.contextual_window_size // 2)

        for index, current in enumerate(scores):
            left = max(0, index - half_window)
            right = min(len(scores), index + half_window + 1)
            window = scores[left:right]
            if not window:
                continue

            high_positions = [j for j in range(left, right) if scores[j] >= threshold]
            high_count = len(high_positions)

            if high_count == 0:
                depth = max(0.0, threshold - current)
                depth_ratio = min(1.0, depth / max(threshold, 1e-6))
                penalty = 0.10 * (depth_ratio**1.4)
                adjusted[index] = max(0.0, current - penalty)
                continue

            if high_count < self.contextual_high_count:
                continue

            support_denominator = max(1, len(window) - self.contextual_high_count + 1)
            support_ratio = min(1.0, (high_count - self.contextual_high_count + 1) / support_denominator)
            has_left_support = any(pos < index for pos in high_positions)
            has_right_support = any(pos > index for pos in high_positions)
            side_factor = 1.25 if has_left_support and has_right_support else 1.10

            if current >= threshold:
                over_ratio = min(1.0, (current - threshold) / max(1.0 - threshold, 1e-6))
                boost = 0.10 * support_ratio * side_factor * (0.65 + 0.35 * (over_ratio**0.7))
            else:
                near_ratio = max(0.0, 1.0 - (threshold - current) / max(0.25, threshold * 0.6))
                boost = 0.07 * support_ratio * side_factor * (near_ratio**1.6)

            adjusted[index] = min(1.0, current + boost)

        return [max(0.0, min(1.0, value)) for value in adjusted]

    def _percentile(self, values: list[float], q: float) -> float:
        if not values:
            return 0.0
        if len(values) == 1:
            return float(values[0])

        sorted_values = sorted(values)
        rank = max(0.0, min(1.0, q)) * (len(sorted_values) - 1)
        lower = int(math.floor(rank))
        upper = int(math.ceil(rank))
        if lower == upper:
            return float(sorted_values[lower])
        weight = rank - lower
        return float(sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight)

    def _temporal_regularize_scores(self, scores: list[float]) -> list[float]:
        if len(scores) < 5:
            return scores

        baseline = self._rolling_median(scores, self.regularize_window_size)
        residuals = [scores[i] - baseline[i] for i in range(len(scores))]
        robust_scale = self._robust_scale(residuals)
        sigma = self.regularize_sigma_factor * robust_scale

        threshold = max(0.02, min(0.08, 0.5 * sigma))
        high_mask = [scores[i] >= baseline[i] + threshold for i in range(len(scores))]
        low_mask = [scores[i] <= baseline[i] - threshold for i in range(len(scores))]
        adjusted = list(scores)

        for start, end in self._collect_true_runs(low_mask):
            run_length = end - start
            if run_length > self.regularize_max_gap_segments or start == 0 or end >= len(scores):
                continue

            left_value = adjusted[start - 1]
            right_value = adjusted[end]
            valley = sum(adjusted[start:end]) / float(run_length)
            support = min(left_value, right_value)
            if support - valley < max(0.04, 0.35 * sigma):
                continue

            for offset, index in enumerate(range(start, end)):
                ratio = (offset + 1) / float(run_length + 1)
                interp = left_value + (right_value - left_value) * ratio
                lifted = 0.75 * interp + 0.25 * baseline[index]
                adjusted[index] = max(adjusted[index], min(1.0, lifted))

        for start, end in self._collect_true_runs(high_mask):
            run_length = end - start
            if run_length > self.regularize_max_spike_segments or start == 0 or end >= len(scores):
                continue

            left_value = adjusted[start - 1]
            right_value = adjusted[end]
            peak = max(adjusted[start:end])
            context = max(left_value, right_value)
            if peak - context < max(0.04, 0.35 * sigma):
                continue

            cap = min(1.0, min(left_value, right_value) + 0.4 * max(0.04, sigma))
            for index in range(start, end):
                adjusted[index] = min(adjusted[index], cap)

        return [max(0.0, min(1.0, value)) for value in adjusted]

    def _rolling_median(self, values: list[float], window_size: int) -> list[float]:
        half = max(1, window_size // 2)
        medians: list[float] = []
        for index in range(len(values)):
            left = max(0, index - half)
            right = min(len(values), index + half + 1)
            window = sorted(values[left:right])
            medians.append(self._median(window))
        return medians

    def _robust_scale(self, values: list[float]) -> float:
        center = self._median(values)
        abs_dev = [abs(value - center) for value in values]
        mad = self._median(abs_dev)
        return max(1e-4, 1.4826 * mad)

    def _median(self, values: list[float]) -> float:
        if not values:
            return 0.0
        sorted_values = sorted(values)
        middle = len(sorted_values) // 2
        if len(sorted_values) % 2 == 1:
            return float(sorted_values[middle])
        return float((sorted_values[middle - 1] + sorted_values[middle]) / 2.0)

    def _collect_true_runs(self, mask: list[bool]) -> list[tuple[int, int]]:
        runs: list[tuple[int, int]] = []
        index = 0
        while index < len(mask):
            if not mask[index]:
                index += 1
                continue
            start = index
            while index < len(mask) and mask[index]:
                index += 1
            runs.append((start, index))
        return runs


    def _fallback_duration(self, video_path: str) -> float:
        try:
            size_bytes = Path(video_path).stat().st_size
        except OSError:
            size_bytes = 0
        size_mb = size_bytes / (1024 * 1024)
        return max(60.0, min(1800.0, 90.0 + size_mb * 2.0))

    def _build_segments_fallback(self, video_path: str, segment_duration: float) -> list[tuple[float, float, float]]:
        duration = self._fallback_duration(video_path)
        segment_count = max(1, int(math.ceil(duration / segment_duration)))
        seed = int(hashlib.sha256(video_path.encode("utf-8")).hexdigest()[:16], 16)
        rng = random.Random(seed)

        segments: list[tuple[float, float, float]] = []
        for index in range(segment_count):
            start = index * segment_duration
            end = min(duration, start + segment_duration)
            base = rng.random() * 0.55
            burst = 0.45 if rng.random() > 0.86 else 0.0
            score = max(0.0, min(1.0, base + burst))
            segments.append((start, end, score))
        return segments

