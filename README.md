# SegmentByMotion (MVP)

Python GUI MVP for emotion-highlight review workflow:
- Stage 1: create task from video path and compute heat segments (real audio features via `librosa` when decodable)
- Stage 2: review heat segments, threshold filtering, label interesting/uninteresting, undo labels
- Review video playback: embedded VLC player with speed control and candidate-only segment playback
- Speaker threshold profile: save/load reusable threshold ranges by `speaker_id`
- Stage 3: external processing stub entry

## Project Structure

- `app.py`: GUI entrypoint
- `src/app/bootstrap.py`: dependency wiring
- `src/infra/schema.sql`: SQLite schema
- `src/services/*`: task ingest, review and stage3 stub
- `src/ui/*`: Tkinter GUI pages
- `tests/test_smoke.py`: basic flow test

## Quick Start

```powershell
pip install -r requirements.txt
python -m unittest -v
python app.py
```

## Notes

- Stage-1 uses `librosa.load(...)` to decode audio from media and extracts per-segment RMS / zero-crossing-rate / onset-strength, then maps to `heat_score` in `[0,1]`.
- For video containers (such as `mkv`), Stage-1 first tries `ffmpeg` extraction (system ffmpeg or `imageio-ffmpeg` bundled binary), then computes features from decoded PCM.
- If decoding is unavailable (e.g. missing backend or unsupported media), the analyzer falls back to deterministic scoring so workflow remains usable.
- Embedded player requires VLC runtime + `python-vlc` package. Candidate playback in Review can loop only threshold-matched segments in the current time window.

