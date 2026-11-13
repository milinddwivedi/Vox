# Recording & Database Provenance Inventory

**Project**: VOX Meeting Studio Phase 6  
**Auditor**: `teamwork_explorer` (`explorer_refinement_audit`)  
**Status**: Reference Audit for Development Asset Preservation  
**Operating Mode**: STRICTLY READ-ONLY AUDIT  

---

## 1. Asset Preservation Principles (Development Context)

1. **Pre-Release Development Environment**:
   The workspace and database reflect ongoing developer testing and algorithm tuning.
2. **Golden Benchmark Preservation**:
   Meetings 64 and 65 represent real recordings and multi-speaker ground-truth benchmark datasets. Under no circumstances should they be removed or modified.
3. **Acoustic Asset Preservation**:
   Earlier recordings (> 600 MB) lacking explicit database rows contain valuable long-form multi-channel audio used for acoustic model benchmarking.
4. **Isolated Test Pollution Identification**:
   Automated tests in `tests/test_meeting_studio_enterprise_phase2.py` accidentally appended rows 67–100 to `meetings` and rows 12–27 to `screen_recordings`. These are identified and documented separately. Zero deletions are performed during Milestones 0–2.

---

## 2. Exhaustive Recording Asset Inventory (`data/recordings/`)

| Recording Directory / Path | Total Size | Duration | Associated Meeting ID | Classification | Notes & Evidence |
|:---|:---|:---|:---|:---|:---|
| `meeting_20261031_020100` | 446.1 MB | 1814.85s (30.2m) | ID 64 (`Everlane - Exporting Linesheet`) | **Golden Benchmark Evidence** | Complete session: `meeting_mixed.wav` (58MB), `meeting_screen.mp4` (155MB), `mic_track.wav` (58MB), `system_track.wav` (58MB). 157 segments, 22 action items. |
| `meeting_20261031_024641` | 495.4 MB | 2680.20s (44.7m) | ID 65 (`Abhilash Mishra`) | **Golden Benchmark Evidence** | Complete session: `meeting_mixed.wav` (85MB), `meeting_screen.mp4` (66MB), `mic_track.wav` (85MB), `system_track.wav` (85MB). 41 segments, 3 actions, 1 decision. |
| `meeting_20261030_190056` | 412.8 MB | 6449.49s (1.79h) | None | **Acoustic Evaluation Asset** | Unprocessed long-form dual-track WAVs (`mic_track.wav` 206MB, `system_track.wav` 206MB). High-value acoustic stress benchmark. |
| `meeting_20261030_013428` | 67.3 MB | 1050.94s (17.5m) | None | **Acoustic Evaluation Asset** | Dual-track WAVs (`mic_track.wav` 33MB, `system_track.wav` 33MB). |
| `meeting_20261026_224408` | 57.3 MB | 506.64s (8.4m) | None | **Acoustic Evaluation Asset** | `meeting_screen.mp4` (24.9MB), dual-track audio (32MB). |
| `meeting_20261030_215613` | 43.5 MB | 368.89s (6.1m) | None | **Acoustic Evaluation Asset** | Screen recording MP4 (19.9MB) and dual-track WAVs (23.6MB). |
| `meeting_20261021_190034` | 45.9 MB | 282.24s (4.7m) | None | **Acoustic Evaluation Asset** | Screen recording MP4 (27.8MB) and dual-track audio (18MB). |
| `meeting_20261026_225313` | 12.4 MB | 71.34s (1.2m) | None | **Acoustic Evaluation Asset** | Screen recording MP4 (7.8MB) and dual-track audio (4.6MB). |
| `meeting_20261021_064952` | 2.6 MB | 14.19s | None | **Acoustic Evaluation Asset** | Dual-track WAVs and screen MP4. |
| `meeting_20261021_035015` | 4.1 MB | ~20s | None | **Video Benchmark Asset** | `meeting_screen.mp4` (4.1MB). |
| `meeting_20261021_062253` | 14.5 MB | ~70s | None | **Video Benchmark Asset** | `meeting_screen.mp4` (14.5MB). |
| `meeting_20261029_220357` | 4.9 MB | ~25s | None | **Video Benchmark Asset** | `meeting_screen.mp4` (4.9MB). |
| `meeting_20261031_015900` | 461 KB | ~3s | None | **Video Benchmark Asset** | `meeting_screen.mp4` (461KB). |
| `screen/screen_20261030_011237_239.mp4` | 593.6 KB | 6.6s | SR ID 7 | **Screen Recording Capture** | Real desktop screen capture MP4 (1080p, 30fps). |
| `screen/screen_20261030_011253_257.mp4` | 21.0 MB | 125.0s | SR ID 8 | **Screen Recording Capture** | Real desktop screen capture MP4 (1080p, 30fps). |
| `screen/screen_20261030_011550_987.mp4` | 12.4 MB | 109.4s | SR ID 9 | **Screen Recording Capture** | Real desktop screen capture MP4 (1080p, 30fps). |
| `screen/screen_20261030_012915_263.mp4` | 48 B | 0.0s | SR ID 10 | **Screen Recording Capture** | 0-duration test placeholder. |
| `screen/screen_20261030_013420_346.mp4` | 197.4 KB | 3.0s | SR ID 11 | **Screen Recording Capture** | Real desktop screen capture MP4. |
| `meeting_20261102_*` (14 directories) | 1,122 B each | 0.0s | IDs 77, 79, 81, 83, 85, 87, 89, 91, 93, 95, 97, 99 | **Test Pollution Directories** | Contain only `capture_diagnostics.json` with zero audio/video media. |

---

## 3. Database Row Provenance (`data/voxlocal.db`)

### 3.1 Meetings Table (37 Rows Total)
- **Genuine Development Meetings (IDs 64 & 65)**:
  - ID 64: `Everlane - Exporting Linesheet` (157 segments, 22 actions).
  - ID 65: `Abhilash Mishra` (41 segments, 3 actions, 1 decision).
  - Both status `completed`, rich metadata, full audio/video assets.
- **Manual Developer Error Session (ID 66)**:
  - `Meeting 2026-10-31 22:33` (0 duration, 0 segments, status `error`).
- **Accidental Test Pollution Rows (IDs 67–100, 34 rows)**:
  - Alternating titles: `'Sprint Planning Standup'` (status `interrupted`) and `'Client Proposal Review'` (status `completed`).
  - Duration: 0.0s for all 34 rows.
  - Transcript Segments: 0 for all 34 rows.
  - Action Items: 0 for all 34 rows.
  - Decisions: 0 for all 34 rows.
  - Provenance: Written by `tests/test_meeting_studio_enterprise_phase2.py` because `self.config.data_dir = test_dir` was assigned, but `AppConfig` only reads `storage_dir`, silently defaulting to `data/`.

### 3.2 Screen Recordings Table (27 Rows Total)
- **Genuine Developer Test Recordings (IDs 7–11, 5 rows)**:
  - Files physically present on disk in `data/recordings/screen/`.
- **Accidental Test Pollution Rows (IDs 12–27, 16 rows)**:
  - Title: `'Demo Recording'`. Duration 0.0s, file size 0 bytes.
  - Non-existent on disk. Written by `test_screen_recording_state_transitions`.

---

## 4. Non-Destructive Prevention & Optional Cleanup Proposal

### 4.1 Architectural Prevention (Permanent Fix)
In `src/voxlocal/core/config.py`, add a property alias on `AppConfig`:
```python
@property
def data_dir(self) -> Path:
    return Path(self.storage_dir)

@data_dir.setter
def data_dir(self, val: str | Path) -> None:
    self.storage_dir = str(val)
    self.ensure_directories()
```
This guarantees that any existing or future test setting `config.data_dir = test_dir` automatically updates `storage_dir`, `db_path`, and `recordings_dir` to the test directory, permanently preventing any accidental write to `data/voxlocal.db`.

### 4.2 Separate Cleanup Proposal (Awaiting User Discretion)
If the user desires to clean up the 34 pollution rows and 14 empty folders, it can be executed as a standalone, atomic SQL transaction without affecting any genuine development data:
```sql
BEGIN TRANSACTION;
DELETE FROM meetings WHERE id BETWEEN 67 AND 100 AND duration_sec = 0.0;
DELETE FROM screen_recordings WHERE id BETWEEN 12 AND 27 AND title = 'Demo Recording';
COMMIT;
```
*Note: This cleanup is strictly optional and will only be executed if explicitly directed by the user.*
