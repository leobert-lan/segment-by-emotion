# Architecture (MVP)

## Layers

- `src/ui`: Tkinter views (`MainWindow`, `ReviewWindow`)
- `src/services`: use-case orchestration (`TaskIngestService`, `ReviewService`)
- `src/infra`: SQLite persistence (`Database`, repositories)
- `src/domain`: data models (`Task`, `Segment`, `ThresholdProfile`)

## Current Flow

1. User imports a video and speaker id from `Task` page.
2. `TaskIngestService` creates task, runs stage-1 analyzer and saves segments.
3. User switches to `Review` page, applies threshold range and labels candidate segments.
4. Label events are persisted for undo/recovery.
5. Speaker threshold can be saved/loaded via profile table.
6. Stage-3 button exists as integration stub for external processing.

## Expansion Points

- Replace `HeatAnalyzer` internals with real audio feature extraction.
- Add background workers and progress states for batch jobs.
- Implement stage-3 queue adapter (API or message queue) with execution tracking.

