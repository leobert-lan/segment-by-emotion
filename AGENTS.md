# AGENTS.md — Segment-By-Emotion

## Big Picture

Python Tkinter MVP for emotion-segment review of video files.

**Layer stack** (strict, no cross-layer imports except upward):
```
src/domain/models.py          ← pure dataclasses (Task, Segment, ThresholdProfile)
src/infra/{db,repositories}   ← SQLite via Database.session() context manager
src/services/{heat,ingest,review_service,stage3_stub}  ← use-case orchestration
src/ui/{main_window,review_window}  ← Tkinter views (no direct DB access)
src/app/bootstrap.py          ← single wiring point; build_app() returns MainWindow
app.py                        ← entrypoint
```

All dependency wiring lives **only** in `src/app/bootstrap.py`. Do not wire elsewhere.

## Task Lifecycle

Tasks move through these `status` values (stored in SQLite):
```
stage1_pending → stage1_running → stage1_done → review_in_progress → review_done
```
Segments are created by `TaskIngestService.run_stage1_for_task()`; `current_label` is NULL (unlabeled), `"interesting"`, or `"uninteresting"`. Label history is stored in `label_events` for undo support.

## HeatAnalyzer Audio Fallback Chain

`HeatAnalyzer._try_load_audio()` in `src/services/heat_service.py` tries in order:
1. **ffmpeg** (system binary or `imageio-ffmpeg` bundled) → PCM WAV via subprocess
2. **librosa** → direct decode
3. **Deterministic fallback** — hash-based `[0,1]` scores derived from the video path; workflow remains fully usable without any audio backend

`heat_score` is always in `[0.0, 1.0]`, derived from RMS / zero-crossing-rate / onset-strength.

## Developer Workflows

```bash
# Install
pip install -r requirements.txt      # librosa, numpy, imageio-ffmpeg, python-vlc

# Test (all tests; no GUI required)
python -m unittest -v

# Run GUI
python app.py
```

Database is auto-created at `data/segment_by_motion.db` on first run.  
Schema source: `src/infra/schema.sql`.

VLC runtime must be installed separately for embedded video playback (`tools/setup_vlc.py` provides install guidance; `tools/configurate_vlc.py` and `tools/vlc_wrapper.py` assist with runtime detection).

## Testing Conventions

- Tests **never** hit real audio — monkey-patch the fallback to force deterministic mode:
  ```python
  analyzer._try_load_audio = lambda _path: (None, None)
  ```
- Each test creates its own `tempfile.TemporaryDirectory` with a fresh SQLite DB and manually calls `database.initialize(schema_path)`.
- `schema_path` is always resolved relative to the test file:
  ```python
  schema_path = Path(__file__).resolve().parents[1] / "src" / "infra" / "schema.sql"
  ```
- UI logic that can be extracted as `@staticmethod` (e.g. `ReviewWindow._build_local_track`, `ReviewWindow._find_focus_segment_index`) is tested without instantiating Tkinter (see `tests/test_review_window_local_track.py`).

## Project-Specific Patterns

- **`Database.session()`** is a context manager that opens, commits, and closes a connection per call — no persistent connection pool.
- **`ThresholdProfile`** is keyed by `(speaker_id, profile_name)` UNIQUE — `profile_name` defaults to `"default"`.
- **Supported video extensions** (in `TaskIngestService.VIDEO_EXTENSIONS`): `.mp4 .mkv .avi .mov .m4v .webm .flv`
- UI labels are in Simplified Chinese — preserve this convention for any new UI strings.
- `Stage3PipelineStub` in `src/services/stage3_stub.py` is an intentional placeholder; replace with a real queue adapter when Stage 3 is implemented.
- `ReviewWindow` is a `ttk.Frame` (not a `Toplevel`) embedded inside `MainWindow`'s page container — page switching is done via `.pack()`/`.pack_forget()`.

## Key Files

| File | Purpose |
|------|---------|
| `src/app/bootstrap.py` | All dependency wiring |
| `src/infra/schema.sql` | Authoritative DB schema |
| `src/services/heat_service.py` | Audio analysis + fallback logic (~500 lines) |
| `src/ui/review_window.py` | Main review UI, VLC integration, local track (~1500 lines) |
| `tests/test_smoke.py` | End-to-end flow reference test |

---

## Android Node (MediaService/)

Kotlin + Jetpack Compose app. Acts as a video-processing node: receives video from Python server via LAN TCP socket, cuts/merges/compresses `interesting` segments, uploads results back.

### Layer Stack

```
domain/model+state   ← pure Kotlin data classes; no Android framework imports
net/protocol         ← @Serializable sealed ControlMessage / DataMessage
net/socket           ← ControlChannelClient (:23010) + DataChannelClient (:23011) + SocketConnectionManager
media/codec          ← HardwareCodecSelector (prefers c2.qti.hevc.encoder on Snapdragon 880)
media/pipeline       ← SegmentCutter → SegmentMerger → VideoCompressor (Media3 Transformer)
storage/db           ← Room: LocalTaskEntity + TransferChunkEntity (resume support)
storage/file         ← FileStoreManager (per-task dir layout)
storage/prefs        ← NodePreferences (DataStore: host, ports, nodeId)
service/             ← MediaNodeService (ForegroundService) + TaskOrchestrator + UploadManager
ui/                  ← ConnectionScreen + NodeStatusScreen + AppNavHost (Compose Navigation)
```

All wiring (DB, prefs, pipeline, connection manager) happens inside `MediaNodeService.handleConnect()` — the Android equivalent of `bootstrap.py`.

### Dual-Channel Protocol
- Control channel port **23010**: newline-delimited JSON (`MessageFramer.encodeControl`).
- Data channel port **23011**: `[4B header-len][JSON header][binary payload]` frames (`MessageFramer.writeDataFrame`).
- **Both channels must connect before task dispatch** (`SocketConnectionManager`).

### Hardware Acceleration
`HardwareCodecSelector.selectEncoder("video/hevc")` iterates `MediaCodecList`; on Snapdragon 880 resolves to `c2.qti.hevc.encoder`. Pipeline: pass-through cut (no transcode) → pass-through merge → single HEVC encode via Media3 `Transformer`.

### Android Node Key Files

| File | Purpose |
|------|---------|
| `MediaService/app/build.gradle.kts` | Dependencies: Media3, Room, KSP, serialization, DataStore |
| `MediaService/gradle/libs.versions.toml` | Version catalog — verify `ksp` version matches `kotlin` |
| `domain/state/TaskState.kt` | Sealed state machine: Idle→Connecting→Receiving→Processing→Uploading→Done/Error |
| `net/protocol/ControlMessage.kt` | All control-channel message types |
| `net/protocol/MessageFramer.kt` | Wire encode/decode for both channels |
| `service/TaskOrchestrator.kt` | Single state-flow writer; drives full lifecycle |
| `service/MediaNodeService.kt` | ForegroundService; `ACTION_CONNECT` / `ACTION_DISCONNECT` |
| `SDS/android_node_design.md` | Full Android architecture design |
| `SRS/android_task_checklist.md` | Milestone task list (M0–M5) |

### Android Developer Workflow
```bash
cd MediaService
./gradlew assembleDebug          # build
./gradlew installDebug           # install to connected device
./gradlew test                   # unit tests (no device needed)
./gradlew connectedAndroidTest   # on-device tests (Snapdragon 880 codec validation)
```
minSdk = 31 (Android 12). VLC not required (uses MediaCodec + Media3 natively).

