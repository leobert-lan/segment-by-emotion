# SegmentByMotion (MVP)

Python GUI MVP for emotion-highlight review workflow:
- Stage 1: create task from video path and compute heat segments (deterministic placeholder analyzer)
- Stage 2: review heat segments, threshold filtering, label interesting/uninteresting, undo labels
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
python -m unittest -v
python app.py
```

## Notes

- Current Stage-1 heat analyzer does not decode audio yet; it produces deterministic scores from file metadata/path to enable workflow validation.
- This keeps the architecture ready for replacing analyzer internals with real audio features later.

