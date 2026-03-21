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


if __name__ == "__main__":
    unittest.main()

