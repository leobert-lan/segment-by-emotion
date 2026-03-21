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
    def __init__(self, target_sr: int = 16000) -> None:
        self.target_sr = target_sr

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

        for index in range(segment_count):
            start_sample = int(index * segment_duration * sample_rate)
            end_sample = min(len(audio), int((index + 1) * segment_duration * sample_rate))
            segment_audio = audio[start_sample:end_sample]
            if len(segment_audio) == 0:
                rms_values.append(0.0)
                zcr_values.append(0.0)
                onset_values.append(0.0)
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

            rms_values.append(rms)
            zcr_values.append(zcr)
            onset_values.append(onset)

        rms_norm = self._minmax_normalize(rms_values)
        zcr_norm = self._minmax_normalize(zcr_values)
        onset_norm = self._minmax_normalize(onset_values)

        scores = [
            max(0.0, min(1.0, 0.55 * rms_norm[i] + 0.25 * zcr_norm[i] + 0.20 * onset_norm[i]))
            for i in range(segment_count)
        ]
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

    def _smooth_scores(self, scores: list[float]) -> list[float]:
        if len(scores) < 3:
            return scores
        smoothed: list[float] = []
        for index, score in enumerate(scores):
            left = scores[index - 1] if index > 0 else score
            right = scores[index + 1] if index < len(scores) - 1 else score
            smoothed.append(max(0.0, min(1.0, 0.2 * left + 0.6 * score + 0.2 * right)))
        return smoothed

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

