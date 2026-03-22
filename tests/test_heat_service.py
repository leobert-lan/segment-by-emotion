import importlib.util
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from src.services.heat_service import HeatAnalyzer


class HeatAnalyzerTest(unittest.TestCase):
    def test_fallback_segments_are_deterministic(self) -> None:
        analyzer = HeatAnalyzer()

        # Force fallback path to validate deterministic behavior independently of local codecs.
        analyzer._try_load_audio = lambda _path: (None, None)  # type: ignore[method-assign]
        path = "C:/tmp/fake_video.mp4"

        first = analyzer.build_segments(path, segment_duration=5.0)
        second = analyzer.build_segments(path, segment_duration=5.0)

        self.assertEqual(first, second)
        self.assertGreater(len(first), 0)
        for _, _, score in first:
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)

    @unittest.skipUnless(importlib.util.find_spec("librosa") is not None, "librosa not installed")
    def test_real_audio_path_emphasizes_burst_segment(self) -> None:
        analyzer = HeatAnalyzer(target_sr=16000)

        with tempfile.TemporaryDirectory() as temp_dir:
            wav_path = Path(temp_dir) / "sample.wav"
            self._write_demo_wav(wav_path)

            segments = analyzer.build_segments(str(wav_path), segment_duration=2.0)
            self.assertGreaterEqual(len(segments), 3)

            scores = [score for _, _, score in segments]
            self.assertTrue(all(0.0 <= score <= 1.0 for score in scores))

            # The last third is synthesized as a stronger/noisier section and should rank higher.
            self.assertGreater(scores[-1], scores[0])

    @unittest.skipUnless(importlib.util.find_spec("librosa") is not None, "librosa not installed")
    def test_target_patterns_score_higher_than_calm_baseline(self) -> None:
        analyzer = HeatAnalyzer(target_sr=16000)

        with tempfile.TemporaryDirectory() as temp_dir:
            wav_path = Path(temp_dir) / "target_patterns.wav"
            self._write_target_pattern_wav(wav_path)

            segments = analyzer.build_segments(str(wav_path), segment_duration=2.0)
            self.assertGreaterEqual(len(segments), 5)
            scores = [score for _, _, score in segments[:5]]

            calm = scores[0]
            sustained_vowel = scores[1]
            high_pitch = scores[2]
            faster_rate = scores[3]
            excited = scores[4]

            self.assertGreater(sustained_vowel, calm)
            self.assertGreater(high_pitch, calm)
            self.assertGreater(faster_rate, calm)
            self.assertGreater(excited, calm)
            self.assertGreater(excited, high_pitch)

    def test_temporal_regularization_bridges_short_low_gap(self) -> None:
        analyzer = HeatAnalyzer()

        scores = [0.18, 0.78, 0.82, 0.30, 0.79, 0.76, 0.20]
        regularized = analyzer._temporal_regularize_scores(scores)

        self.assertGreater(regularized[3], scores[3])
        self.assertGreater(regularized[3], 0.60)

    def test_temporal_regularization_suppresses_short_isolated_spike(self) -> None:
        analyzer = HeatAnalyzer()

        scores = [0.20, 0.22, 0.80, 0.19, 0.18, 0.17, 0.16]
        regularized = analyzer._temporal_regularize_scores(scores)

        self.assertLess(regularized[2], scores[2])
        self.assertLess(regularized[2], 0.35)

    def test_temporal_regularization_does_not_fill_long_low_valley(self) -> None:
        analyzer = HeatAnalyzer()

        scores = [0.18, 0.80, 0.22, 0.21, 0.23, 0.79, 0.18]
        regularized = analyzer._temporal_regularize_scores(scores)

        self.assertLessEqual(regularized[2], 0.35)
        self.assertLessEqual(regularized[3], 0.35)
        self.assertLessEqual(regularized[4], 0.35)

    def _write_demo_wav(self, output_path: Path, sample_rate: int = 16000) -> None:
        duration_sec = 6
        total_samples = sample_rate * duration_sec

        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)

            frames = bytearray()
            for i in range(total_samples):
                t = i / sample_rate
                if t < 2.0:
                    amplitude = 0.08
                    frequency = 220.0
                    value = amplitude * math.sin(2.0 * math.pi * frequency * t)
                elif t < 4.0:
                    amplitude = 0.15
                    frequency = 220.0
                    value = amplitude * math.sin(2.0 * math.pi * frequency * t)
                else:
                    amplitude = 0.42
                    frequency = 440.0
                    pulse = 1.0 if int(t * 12) % 2 == 0 else -1.0
                    value = amplitude * math.sin(2.0 * math.pi * frequency * t) + 0.08 * pulse

                value = max(-1.0, min(1.0, value))
                frames.extend(struct.pack("<h", int(value * 32767)))

            wav_file.writeframes(frames)

    def _write_target_pattern_wav(self, output_path: Path, sample_rate: int = 16000) -> None:
        duration_sec = 10
        total_samples = sample_rate * duration_sec

        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)

            frames = bytearray()
            for i in range(total_samples):
                t = i / sample_rate

                if t < 2.0:
                    # Calm baseline: low energy and low pitch.
                    amplitude = 0.07
                    frequency = 180.0
                    value = amplitude * math.sin(2.0 * math.pi * frequency * t)
                elif t < 4.0:
                    # Sustained vowel-like segment: stable voiced tone.
                    amplitude = 0.22
                    frequency = 250.0
                    value = amplitude * math.sin(2.0 * math.pi * frequency * t)
                elif t < 6.0:
                    # High-pitch segment.
                    amplitude = 0.25
                    frequency = 680.0
                    value = amplitude * math.sin(2.0 * math.pi * frequency * t)
                elif t < 8.0:
                    # Faster-rate segment with stronger short-term change.
                    amplitude = 0.24
                    frequency = 250.0 if int(t * 20) % 2 == 0 else 520.0
                    value = amplitude * math.sin(2.0 * math.pi * frequency * t)
                else:
                    # Excited segment: high energy + higher pitch + roughness.
                    amplitude = 0.48
                    frequency = 760.0
                    pulse = 1.0 if int(t * 24) % 2 == 0 else -1.0
                    value = amplitude * math.sin(2.0 * math.pi * frequency * t) + 0.12 * pulse

                value = max(-1.0, min(1.0, value))
                frames.extend(struct.pack("<h", int(value * 32767)))

            wav_file.writeframes(frames)


if __name__ == "__main__":
    unittest.main()

