import hashlib
import random
from pathlib import Path


class HeatAnalyzer:
    def estimate_duration_sec(self, video_path: str) -> float:
        try:
            size_bytes = Path(video_path).stat().st_size
        except OSError:
            size_bytes = 0
        size_mb = size_bytes / (1024 * 1024)
        # Use a deterministic proxy for duration when no decoding backend is configured yet.
        return max(60.0, min(1800.0, 90.0 + size_mb * 2.0))

    def build_segments(self, video_path: str, segment_duration: float) -> list[tuple[float, float, float]]:
        duration = self.estimate_duration_sec(video_path)
        segment_count = int(duration // segment_duration)
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

