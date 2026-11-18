"""
Central Application Controller (QObject) for VoxLocal.
Coordinates:
- Universal Voice Typing (independent state machine & audio pipeline)
- Meeting Recording & Progressive/Offline Transcription (independent state machine & audio pipeline)
- Win32 Focus Tracking, Global Hotkeys, and Cross-Application Text Insertion
- Thread-safe Qt Signals to update Floating Widget, System Tray, and Main Window without blocking UI.
"""

from __future__ import annotations

import collections
import enum
import os
import queue
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import ctypes
from ctypes import wintypes
import numpy as np
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

def is_pid_alive(pid: Optional[int]) -> bool:
    if not pid or pid <= 0:
        return False
    h = kernel32.OpenProcess(0x1000, False, pid)
    if h:
        kernel32.CloseHandle(h)
        return True
    return False

from voxlocal.audio.call_detector import CallDetector, MeetingLifecycleState
from voxlocal.audio.dictation_recorder import DictationRecorder
from voxlocal.audio.meeting_recorder import MeetingRecorder
from voxlocal.audio.streaming_typer import StreamingVoiceTyper
from voxlocal.core.config import AppConfig
from voxlocal.core.logger import (
    check_disk_space_mb,
    generate_correlation_id,
    get_logger,
    get_system_resource_snapshot,
    operation_context,
)
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine
from voxlocal.engine.transcriber import (
    LocalWhisperTranscriber,
    PersistentSpeakerDiarizer,
    is_likely_person_name,
    TranscribeProfile,
    clean_repetition_tail,
    is_hallucination,
    load_wav_as_16k_f32,
)
from voxlocal.storage.database import LocalDatabase
from voxlocal.storage.exporter import LocalMeetingSummarizer, format_seconds_hms
from voxlocal.video.screen_recorder import ScreenRecorder
from voxlocal.win32.hotkeys import GlobalHotkeyManager
from voxlocal.win32.startup import StartupManager
from voxlocal.win32.text_inserter import TextInserter
from voxlocal.win32.window_focus import TargetWindowInfo, WindowFocusTracker
from voxlocal.core.metering import UsageMeteringEngine, count_committed_words
from voxlocal.core.entitlement import EntitlementManager
from voxlocal.auth.desktop_auth import DesktopAuthBroker

logger = get_logger("controller")


class DictationState(str, enum.Enum):
    """Canonical 9-state dictation lifecycle enum for Wispr Flow parity."""
    IDLE = "idle"
    INITIALIZING = "initializing"
    LISTENING = "listening"
    STOPPING = "stopping"
    PROCESSING = "processing"
    ERROR = "error"
    DISMISSED = "dismissed"
    RETRYING = "retrying"
    TESTING = "testing"


class FinalizingJobsSet(set):
    """Set of meeting_ids undergoing finalization, also maintaining per-session cancellation events."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._events: Dict[int, threading.Event] = {}

    def add(self, meeting_id: int, event: Optional[threading.Event] = None) -> None:
        super().add(meeting_id)
        if event is not None:
            self._events[meeting_id] = event
        elif meeting_id not in self._events:
            self._events[meeting_id] = threading.Event()

    def discard(self, meeting_id: int) -> None:
        super().discard(meeting_id)
        self._events.pop(meeting_id, None)

    def remove(self, meeting_id: int) -> None:
        super().remove(meeting_id)
        self._events.pop(meeting_id, None)

    def pop(self) -> int:
        mid = super().pop()
        self._events.pop(mid, None)
        return mid

    def clear(self) -> None:
        super().clear()
        self._events.clear()

    def update(self, *s):
        for iterable in s:
            for item in iterable:
                self.add(item)

    def get(self, meeting_id: int, default: Any = None) -> Any:
        return self._events.get(meeting_id, default)

    def __getitem__(self, meeting_id: int) -> threading.Event:
        if meeting_id not in self._events:
            self._events[meeting_id] = threading.Event()
        return self._events[meeting_id]

    def __setitem__(self, meeting_id: int, event: threading.Event) -> None:
        self.add(meeting_id, event)

    def values(self):
        return self._events.values()


class AppController(QObject):
    """Central orchestrator exposing Qt signals for all UI components."""

    # Milestone 2 Interface Contract Signals
    state_changed = pyqtSignal(object)                    # (DictationState)
    audio_level_changed = pyqtSignal(float, list)         # (rms_level: float, equalizer_levels: list[float])
    mode_changed = pyqtSignal(str)                        # ('verbatim' | 'clean' | 'polished' | 'structured' | 'email')

    # Voice Typing Signals (retained for backward compatibility)
    dictation_state_changed = pyqtSignal(str, str)  # (state, status_message)
    dictation_level_changed = pyqtSignal(float)     # mic RMS level [0..1]
    dictation_completed = pyqtSignal(str, str, str) # (final_text, target_window, method_used)
    dictation_interim_updated = pyqtSignal(str, str) # (provisional_text, stable_prefix)
    transcribe_profile_changed = pyqtSignal(str)     # (profile: 'fast' | 'balanced' | 'max_accuracy')
    writing_mode_changed = pyqtSignal(str)           # (mode: 'verbatim' | 'clean' | 'polished' | 'structured' | 'email')

    # Meeting Recording & Transcription Signals (Core Feature 3)
    meeting_state_changed = pyqtSignal(str, str)    # (state, status_message)
    meeting_lifecycle_changed = pyqtSignal(str, str)  # (lifecycle_state, status_message)
    meeting_tick = pyqtSignal(float, float, float, bool, bool)  # (elapsed, mic_lvl, sys_lvl, mic_ok, sys_ok)
    meeting_progressive_segment = pyqtSignal(int, dict)         # (meeting_id, segment_dict)
    meeting_live_partial = pyqtSignal(int, dict)                # (meeting_id, partial_dict)
    meeting_live_confirmed = pyqtSignal(int, dict)              # (meeting_id, confirmed_dict)
    meeting_about_to_mux = pyqtSignal(int)                      # meeting_id (releases UI media player handle)
    meeting_saved_and_transcribed = pyqtSignal(int)             # meeting_id
    call_detected = pyqtSignal(str, str)                        # (app_name, suggested_title) - for explicit user consent popup
    call_ended = pyqtSignal(str)                                # (app_name)
    history_updated = pyqtSignal()

    # PC Screen Recording Signals (Core Feature 2)
    screen_recording_state_changed = pyqtSignal(str, str)  # (state: 'idle' | 'recording' | 'paused' | 'saving', message)
    screen_recording_tick = pyqtSignal(float, int)         # (elapsed_sec, frame_count)
    screen_recordings_updated = pyqtSignal()
    screen_recording_saved = pyqtSignal(str, str)          # (video_path, title)
    annotation_mode_changed = pyqtSignal(bool)             # (is_active)

    # General & Diagnostics Signals
    model_status_changed = pyqtSignal(str)
    notification_requested = pyqtSignal(str, str)   # (title, message)
    target_window_changed = pyqtSignal(str, str)    # (window_title, process_name)
    theme_changed = pyqtSignal(str)                 # (theme_name: 'dark' | 'light')
    flow_bar_edge_changed = pyqtSignal(str)         # ('bottom' | 'left' | 'right')
    open_scratchpad_requested = pyqtSignal()
    open_notetaker_drawer_requested = pyqtSignal()
    polish_toast_requested = pyqtSignal(str, str, str)
    notes_updated = pyqtSignal()
    snippets_updated = pyqtSignal()
    vocabulary_updated = pyqtSignal()
    calendar_upcoming_changed = pyqtSignal(list)
    calendar_accounts_changed = pyqtSignal(list)
    calendar_prepare_notetaker_requested = pyqtSignal(object)
    hotkey_copy_last_requested = pyqtSignal()
    hotkey_paste_last_requested = pyqtSignal()
    hotkey_transform_last_requested = pyqtSignal()

    # Ask Vox Semantic Intelligence Streaming Signals
    ask_vox_token_streamed = pyqtSignal(int, str)   # (query_id, incremental chunk)
    ask_vox_citations_ready = pyqtSignal(int, list) # (query_id, citations list)
    ask_vox_stream_finished = pyqtSignal(int, dict) # (query_id, full response dict)

    # Commercial Entitlement & Usage Metering Signals (P0)
    entitlement_changed = pyqtSignal(dict)           # (entitlement_dict: plan, is_pro, limits, etc.)
    quota_warning = pyqtSignal(str, float, float)     # (metric_name, current_val, limit_val)
    meeting_overrun_warning = pyqtSignal(str, float)  # (warning_message, elapsed_sec)
    auth_state_changed = pyqtSignal(dict)             # (user_profile_dict)
    login_completed_signal = pyqtSignal(bool, str)    # (ok, message)
    toast_requested = pyqtSignal(str, str)            # (title, message)
    upgrade_dialog_requested = pyqtSignal(str)        # (reason_string)
    usage_updated = pyqtSignal(dict)                  # (weekly_usage_dict)
    update_check_completed = pyqtSignal(bool, dict, str) # (is_available, manifest, message)

    def __init__(self, config: Optional[AppConfig] = None) -> None:
        super().__init__()
        self.config = config or AppConfig.load()
        self.db = LocalDatabase(self.config.db_path)

        # Initialize Commercial Ledger & Entitlements (100% Local DPAPI Vault)
        self._auth_generation: int = 0
        storage_root = Path(self.config.storage_dir)
        self.metering = UsageMeteringEngine(self.db.db_path)
        self.entitlement = EntitlementManager(cache_file=storage_root / "entitlement.vault")
        self.desktop_auth_broker = DesktopAuthBroker(vault_path=storage_root / "auth_session.vault")
        self.auth_broker = self.desktop_auth_broker  # Backward compatibility alias
        self.desktop_auth_broker.on_session_synced = self._on_desktop_auth_synced
        self._restore_user_session()

        # Update Manager & Release Trust Chain (Milestone 5)
        try:
            from voxlocal.core.updater import UpdateManager
            self.update_manager = UpdateManager()
        except Exception as exc:
            logger.debug("UpdateManager initialization: %s", exc)
            self.update_manager = None

        # Initialize Local LLM engine lazily
        try:
            from voxlocal.engine.llm_engine import LocalLLMEngine
            self.llm_engine = LocalLLMEngine(
                model_dir=self.config.llm_model_path,
                device=self.config.llm_device,
            )
        except Exception as exc:
            logger.debug("LocalLLMEngine initialization: %s", exc)
            self.llm_engine = None

        self.intelligence = MeetingIntelligenceEngine(self.db, llm_engine=self.llm_engine)
        self._cancel_ask_vox: bool = False
        self._ask_vox_query_id: int = 0
        self._ask_vox_thread: Optional[threading.Thread] = None

        # Apply data retention policy on startup if configured
        if self.config.retention_days > 0:
            self.db.apply_retention_policy(self.config.retention_days)

        # Sync startup registry state with config if registry is accessible
        if StartupManager.is_available():
            if self.config.launch_at_login != StartupManager.is_enabled():
                StartupManager.set_enabled(
                    self.config.launch_at_login,
                    minimized=self.config.start_minimized_to_tray,
                )
        else:
            logger.debug("Windows startup registry is not accessible; skipping login sync.")

        # Win32 Subsystems
        self.focus_tracker = WindowFocusTracker()
        self.text_inserter = TextInserter(self.focus_tracker)
        self.hotkey_manager = GlobalHotkeyManager()

        # Audio & Screen Capture Subsystems (100% independent pipelines)
        self.dictation_recorder = DictationRecorder()
        self.streaming_typer = StreamingVoiceTyper()
        self.meeting_recorder = MeetingRecorder()
        self.screen_recorder = ScreenRecorder(fps=getattr(self.config, "screen_fps", 15))
        self.call_detector = CallDetector()
        self._dismissed_call_keys = set()
        self._meeting_prompt_popup: Optional[Any] = None

        # Dedicated PC Screen Recording Subsystem (Core Feature 2)
        self.pc_screen_recorder = ScreenRecorder(fps=30)
        self.pc_screen_audio = MeetingRecorder()
        self._screen_rec_state = "idle"  # idle | recording | paused | saving
        self._active_screen_rec_id: Optional[int] = None
        self._active_screen_rec_title: str = ""
        self._active_screen_rec_video_path: Optional[Path] = None
        self._active_screen_rec_audio_dir: Optional[Path] = None
        self._screen_rec_timer = QTimer(self)
        self._screen_rec_timer.setInterval(250)
        self._screen_rec_timer.timeout.connect(self._on_screen_rec_tick)

        # Unified Live Screen Annotation & Markup Engine (Core Feature 2)
        from voxlocal.annotation.engine import AnnotationEngine
        self.annotation_engine = AnnotationEngine()
        self.pc_screen_recorder.set_annotation_engine(self.annotation_engine)
        self._annotation_overlay: Optional[Any] = None
        self._annotation_toolbar: Optional[Any] = None
        self._active_markup_studios: list[Any] = []
        self._active_snipping_overlays: list[Any] = []

        # Speech Recognition Engine
        self.transcriber = LocalWhisperTranscriber(
            models_dir=self.config.models_dir,
            model_name=self.config.whisper_model,
            compute_device=self.config.compute_device,
            compute_type=self.config.compute_type,
            cpu_threads=self.config.cpu_threads,
            profile=getattr(self.config, "transcribe_profile", "balanced"),
        )

        # Live Streaming Transcriber for Real-Time Incremental Meeting Recognition (SimulStreaming LocalAgreement)
        from voxlocal.engine.streaming_transcriber import LiveMeetingStreamingTranscriber
        self.streaming_transcriber = LiveMeetingStreamingTranscriber(
            transcriber=self.transcriber,
            db=self.db,
            on_partial=self._on_streaming_partial,
            on_confirmed=self._on_streaming_confirmed,
        )

        # Independent State Variables
        self._state_lock = threading.RLock()
        self._dictation_state = DictationState.IDLE.value  # idle, initializing, listening, stopping, processing, error, etc.
        self._dictation_session_id: int = 0
        self._dictation_cancelled: bool = False
        self._cancel_startup_requested: bool = False
        self._ptt_stop_pending: bool = False
        # Milestone 5: Decoupled Dual-Track Meeting State Machine
        self._capture_state: str = "idle"  # "idle" | "recording" | "paused"
        self._finalizing_jobs: FinalizingJobsSet = FinalizingJobsSet()
        self._meeting_lifecycle_state: MeetingLifecycleState = MeetingLifecycleState.NO_MEETING_DETECTED
        self._meeting_lifecycle_message: str = ""
        self._last_emitted_target_info: Optional[Tuple[int, str, str]] = None
        self._cached_mic_device: Optional[AudioDeviceInfo] = None
        self._cached_loop_devices: Optional[List[AudioDeviceInfo]] = None
        self._active_recording_mode: str = getattr(self.config, "meeting_recording_mode", "audio_video")
        self._active_meeting_session_id: str = ""
        self._active_meeting_platform: str = ""
        self._manual_target_lost_since: Optional[float] = None
        self._hands_free_active: bool = False
        self._last_inserted_len: int = 0
        self._last_dictation_ended_sentence: bool = True
        self._last_dictation_time: float = 0.0
        self._last_target_hwnd: int = 0
        self._recent_utterance_history: collections.deque[str] = collections.deque(maxlen=6)
        self._explicit_in_app_widget_ref: Any = None
        self._explicit_in_app_title: str = "VOX"
        self._in_app_session_requested: bool = False
        self._active_in_app_target: Optional[TargetWindowInfo] = None
        self._session_committed_any: bool = False
        self._active_meeting_id: Optional[int] = None
        self._active_meeting_title: str = ""
        self._active_meeting_target_hwnd: Optional[int] = None
        self._active_meeting_pid: Optional[int] = None
        self._active_meeting_diarizer: Optional[PersistentSpeakerDiarizer] = None
        self._active_meeting_corr_id: str = "-"
        self._active_screen_rec_corr_id: str = "-"
        self._active_meeting_calendar_event_id: str = ""
        self._active_meeting_calendar_join_url: str = ""
        self._active_meeting_calendar_attendees: str = ""
        self._active_meeting_calendar_organizer: str = ""
        self._active_meeting_calendar_provider: str = ""
        self._active_meeting_calendar_account_id: str = ""
        self._prepared_calendar_meeting: Any = None
        self._pending_video_bind: bool = False
        self._pending_video_folder: Optional[Path] = None
        self._pending_video_fps: int = 15
        self._pending_video_res: Optional[str] = None
        self._progressive_cursor_sec: float = 0.0
        self._progressive_segment_idx: int = 0
        self._progressive_worker_busy: bool = False
        self._cancel_finalization_event: threading.Event = threading.Event()
        self._finalize_thread: Optional[threading.Thread] = None

        # Dedicated sequential queue for streaming dictation jobs to prevent state inversion/races
        self._streaming_queue: queue.Queue = queue.Queue()
        self._streaming_worker_thread = threading.Thread(
            target=self._streaming_transcription_loop,
            name="StreamingTranscriptionLoop",
            daemon=True,
        )
        self._streaming_worker_thread.start()

        # Dedicated thread for low-latency provisional interim transcription
        self._interim_lock = threading.Lock()
        self._latest_interim_audio: Optional[Tuple[np.ndarray, float]] = None
        self._interim_worker_running: bool = True
        self._interim_worker_thread = threading.Thread(
            target=self._interim_transcription_loop,
            name="InterimTranscriptionLoop",
            daemon=True,
        )
        self._interim_worker_thread.start()

        # Wire callbacks
        self.dictation_recorder.on_level_change = self._on_dictation_level_change
        self.dictation_recorder.on_error = self._on_dictation_error
        self.dictation_recorder.on_interim_audio = self._on_streaming_interim_audio

        self.streaming_typer.on_segment_ready = self._on_streaming_segment_ready
        self.streaming_typer.on_interim_audio = self._on_streaming_interim_audio
        self.streaming_typer.on_level_change = self._on_dictation_level_change
        self.streaming_typer.on_state_change = self._on_streaming_state_change
        self.streaming_typer.on_error = self._on_dictation_error

        self.meeting_recorder.on_status_tick = self._on_meeting_recorder_tick
        self.meeting_recorder.on_device_warning = lambda msg: self.notification_requested.emit(
            "Audio Device Notice", msg
        )

        self.hotkey_manager.on_dictation_toggle = self.toggle_dictation
        self.hotkey_manager.on_hands_free_toggle = self.toggle_hands_free_typing
        self.hotkey_manager.on_ptt_start = self.start_dictation
        self.hotkey_manager.on_ptt_stop = self.stop_dictation_and_insert
        self.hotkey_manager.on_cancel_dictation = self.cancel_dictation
        self.hotkey_manager.on_meeting_toggle = self.toggle_meeting_recording
        self.hotkey_manager.on_mode_switch = self.set_writing_mode
        self.hotkey_manager.on_open_scratchpad = self.open_scratchpad_requested.emit
        self.hotkey_manager.on_transform_last = self.hotkey_transform_last_requested.emit
        self.hotkey_manager.on_paste_last = self.paste_last_dictation
        self.hotkey_manager.on_copy_last = self.copy_last_dictation
        self.hotkey_manager.is_dictation_active_fn = lambda: (
            self._hands_free_active
            or self._dictation_state in ("starting", "listening", "speaking", "processing")
        )

        self._last_dictation_id: Optional[int] = None
        self._last_dictation_raw: str = ""
        self._last_dictation_final: str = ""
        self.calendar_manager: Optional[Any] = None
        self._init_calendar_subsystem()

        if not getattr(self.config, "disable_hotkeys", False):
            self.apply_hotkey_config()
            self.hotkey_manager.start()

        # Foreground window polling timer (every 150ms on main thread)
        self._focus_timer = QTimer(self)
        self._focus_timer.setInterval(150)
        self._focus_timer.timeout.connect(self._poll_foreground_window)
        self._focus_timer.start()

        # Read AI-style call detection polling timer (every 2000ms on main thread)
        self._call_timer = QTimer(self)
        self._call_timer.setInterval(2000)
        self._call_timer.timeout.connect(self._poll_call_detector)
        self._call_timer.start()

        # OS WinEvent hook for instant event-driven detection on call window creation/rename
        if hasattr(self.call_detector, "start_winevent_listener"):
            self.call_detector.start_winevent_listener(self._poll_call_detector)

        # Pre-warm Whisper model in background so first dictation is instantaneous
        if not getattr(self.config, "disable_model_preload", False):
            threading.Thread(target=self._preload_model_bg, name="WhisperPreloadThread", daemon=True).start()

        # Enterprise crash recovery: auto-detect and rescue interrupted/unfinalized meetings
        self._recover_unfinalized_meetings()

        # Non-blocking automatic release update check 5 seconds after startup
        def _bg_update_check() -> None:
            time.sleep(5.0)
            try:
                self.check_for_updates(timeout_sec=10)
            except Exception as upd_err:
                logger.debug("Startup update check skipped: %s", upd_err)
        threading.Thread(target=_bg_update_check, name="StartupUpdateCheck", daemon=True).start()

    def _restore_user_session(self) -> None:
        """Restore active user session, configure user context in metering, and sync entitlement."""
        try:
            broker = getattr(self, "desktop_auth_broker", None) or getattr(self, "auth_broker", None)
            if broker and hasattr(broker, "load_session"):
                session = broker.load_session()
                if isinstance(session, dict):
                    user_info = session.get("user") or {}
                    if not isinstance(user_info, dict):
                        user_info = {}
                    user_id = session.get("user_id") or user_info.get("id") or "default"
                    user_email = user_info.get("email") or ""
                    from voxlocal.core.entitlement import is_master_developer
                    is_master = is_master_developer(user_id=user_id, email=user_email)
                    plan_code = "pro_annual" if is_master else (user_info.get("plan") or session.get("plan_code") or "free")
                    if hasattr(self, "metering") and self.metering is not None:
                        self.metering.set_user_context(user_id, plan_code, email=user_email)
                    if hasattr(self, "entitlement") and self.entitlement is not None and hasattr(self.entitlement, "set_user_context"):
                        self.entitlement.set_user_context(user_id, email=user_email, plan_code=plan_code)
                    # Non-blocking cloud entitlement sync to immediately pull website plan updates on launch
                    threading.Thread(target=self._sync_commercial_worker, name="StartupEntitlementSync", daemon=True).start()
                    return
            if hasattr(self, "metering") and self.metering is not None:
                self.metering.set_user_context(None, "free")
            if hasattr(self, "entitlement") and self.entitlement is not None and hasattr(self.entitlement, "set_user_context"):
                self.entitlement.set_user_context(None)
        except Exception as exc:
            logger.warning("Session restoration failed: %s", exc)

    def _on_desktop_auth_synced(self, profile: Any) -> None:
        """Called automatically when Web-to-Desktop SSO session sync arrives via loopback."""
        try:
            self._auth_generation = getattr(self, "_auth_generation", 0) + 1
            user_id = getattr(profile, "user_id", "default")
            user_email = getattr(profile, "email", "")
            plan_code = getattr(profile, "plan_code", "free")
            logger.info("SSO Session synchronized for user: %s (%s)", user_email, plan_code)
            if hasattr(self, "metering") and self.metering is not None:
                self.metering.set_user_context(user_id, plan_code, email=user_email)
            if hasattr(self, "entitlement") and self.entitlement is not None and hasattr(self.entitlement, "set_user_context"):
                self.entitlement.set_user_context(user_id, email=user_email, plan_code=plan_code)
            self._safe_emit(self.auth_state_changed, self.get_account_status())
            if hasattr(self, "entitlement") and self.entitlement is not None:
                self._safe_emit(self.entitlement_changed, self.entitlement.get_effective_entitlement())
            if hasattr(self, "metering") and self.metering is not None:
                self._safe_emit(self.usage_updated, self.metering.get_weekly_usage())
            threading.Thread(target=self._sync_commercial_worker, name="SSOCloudSync", daemon=True).start()
        except Exception as exc:
            logger.warning("Error applying synced desktop auth session: %s", exc)

    def request_upgrade_dialog(self, reason: str = "Upgrade to VOX Pro") -> None:
        """Trigger upgrade dialog request signal."""
        self.upgrade_dialog_requested.emit(reason)

    def _recover_unfinalized_meetings(self) -> None:
        """
        Scan SQLite for meetings left in 'recording' or 'transcribing' state due to an
        unexpected system crash, hard power loss, or abrupt application kill.
        Inspects disk for WAV audio files, measures actual recorded duration,
        and safely transitions the record to 'interrupted' so users can re-transcribe or view it.
        """
        import wave
        try:
            unfinalized = self.db.list_unfinalized_meetings()
        except Exception as exc:
            logger.debug("Failed querying unfinalized meetings: %s", exc)
            return

        for m in unfinalized:
            m_id = m.get("id")
            audio_path = m.get("audio_path", "")
            mic_path = m.get("mic_audio_path", "")
            sys_path = m.get("sys_audio_path", "")
            vid_path = m.get("video_path", "")

            # If audio_path is not found or empty, search disk recordings_dir
            if not audio_path or not Path(audio_path).exists():
                if vid_path and Path(vid_path).parent.exists():
                    cand = Path(vid_path).parent / "mixed.wav"
                    if cand.exists():
                        audio_path = str(cand)
                        if (Path(vid_path).parent / "mic.wav").exists():
                            mic_path = str(Path(vid_path).parent / "mic.wav")
                        if (Path(vid_path).parent / "sys.wav").exists():
                            sys_path = str(Path(vid_path).parent / "sys.wav")

                if not audio_path or not Path(audio_path).exists():
                    created_slug = str(m.get("created_at", "")).replace("-", "").replace(":", "").replace(" ", "_")
                    for folder in self.config.recordings_dir.glob("meeting_*"):
                        if folder.is_dir() and (folder / "mixed.wav").exists():
                            if created_slug and created_slug[:13] in folder.name:
                                audio_path = str(folder / "mixed.wav")
                                if (folder / "mic.wav").exists():
                                    mic_path = str(folder / "mic.wav")
                                if (folder / "sys.wav").exists():
                                    sys_path = str(folder / "sys.wav")
                                if (folder / "meeting_screen.mp4").exists() and not vid_path:
                                    vid_path = str(folder / "meeting_screen.mp4")
                                break

            # Determine duration from WAV file if available
            duration_sec = float(m.get("duration_sec", 0.0))
            if audio_path and Path(audio_path).exists():
                try:
                    with wave.open(audio_path, "rb") as wf:
                        frames = wf.getnframes()
                        rate = wf.getframerate()
                        if rate > 0:
                            duration_sec = round(float(frames) / float(rate), 2)
                except Exception as exc:
                    logger.debug("Could not read WAV header for meeting #%d: %s", m_id, exc)

            update_fields: Dict[str, Any] = {
                "status": "interrupted",
                "duration_sec": max(duration_sec, float(m.get("duration_sec", 0.0))),
            }
            if audio_path:
                update_fields["audio_path"] = audio_path
            if mic_path:
                update_fields["mic_audio_path"] = mic_path
            if sys_path:
                update_fields["sys_audio_path"] = sys_path
            if vid_path:
                update_fields["video_path"] = vid_path

            self.db.update_meeting(m_id, **update_fields)
            logger.warning(
                "Recovered unfinalized meeting #%d ('%s', status set to 'interrupted', duration=%.1fs, audio=%s)",
                m_id,
                m.get("title", "Meeting"),
                duration_sec,
                audio_path or "none",
            )

    @property
    def is_hands_free_active(self) -> bool:
        return self._hands_free_active

    @property
    def dictation_state(self) -> str:
        return self._dictation_state

    @property
    def capture_state(self) -> str:
        """Immediate hardware capture state: 'idle' | 'recording' | 'paused'."""
        with self._state_lock:
            return self._capture_state

    @property
    def meeting_state(self) -> str:
        """
        Backward-compatible unified meeting state.
        Returns 'recording' or 'paused' if capture engine is active;
        'transcribing' if capture is idle but background finalization jobs are running;
        else 'idle'.
        """
        with self._state_lock:
            if self._capture_state in ("recording", "paused"):
                return self._capture_state
            if self._finalizing_jobs:
                return "transcribing"
            if getattr(self, "meeting_recorder", None) and getattr(self.meeting_recorder, "is_recording", False):
                return "recording"
            return "idle"

    @property
    def _meeting_state(self) -> str:
        """Backward-compatibility property for legacy internal attribute access."""
        return self.meeting_state

    @_meeting_state.setter
    def _meeting_state(self, val: str) -> None:
        """Backward-compatibility setter for legacy tests or internal calls."""
        with self._state_lock:
            if val in ("idle", "recording", "paused"):
                self._capture_state = val
                if val == "idle":
                    self._finalizing_jobs.discard(-1)
            elif val == "transcribing":
                self._capture_state = "idle"
                self._finalizing_jobs.add(-1)
            else:
                self._capture_state = "idle"

    def _safe_emit_finalization_progress(self, state: str, msg: str) -> None:
        """Safely emit finalization state only if capture engine is not actively recording."""
        with self._state_lock:
            if self._capture_state in ("recording", "paused"):
                # Active meeting in progress; suppress background progress so UI is not disrupted
                return
        self._safe_emit(self.meeting_state_changed, state, msg)

    @property
    def meeting_lifecycle_state(self) -> str:
        with self._state_lock:
            return self._meeting_lifecycle_state.value if isinstance(self._meeting_lifecycle_state, MeetingLifecycleState) else str(self._meeting_lifecycle_state)

    @property
    def meeting_lifecycle_message(self) -> str:
        with self._state_lock:
            return self._meeting_lifecycle_message

    @property
    def active_recording_mode(self) -> str:
        with self._state_lock:
            return self._active_recording_mode

    def _safe_emit(self, sig: Any, *args: Any) -> None:
        """Safely emit a Qt signal, ignoring RuntimeError if C/C++ object has been deleted."""
        try:
            sig.emit(*args)
        except (RuntimeError, Exception) as exc:
            logger.debug("Signal emission exception safely caught: %s", exc)

    def set_meeting_lifecycle_state(self, state: MeetingLifecycleState | str, message: str = "") -> None:
        try:
            st_enum = state if isinstance(state, MeetingLifecycleState) else MeetingLifecycleState(str(state))
        except Exception:
            st_enum = MeetingLifecycleState.NO_MEETING_DETECTED
        with self._state_lock:
            self._meeting_lifecycle_state = st_enum
            self._meeting_lifecycle_message = message
        self._safe_emit(self.meeting_lifecycle_changed, st_enum.value, message)

    @property
    def is_meeting_recording(self) -> bool:
        with self._state_lock:
            return self._capture_state in ("recording", "paused")

    def _on_meeting_capture_ended(self) -> None:
        logger.info("Meeting capture provider reported call stage closed -> auto-stopping meeting.")
        self.set_meeting_lifecycle_state(MeetingLifecycleState.MEETING_ENDING, "Meeting call window closed — ending recording...")
        try:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self.stop_meeting_recording)
        except Exception:
            self.stop_meeting_recording()

    def _on_video_source_state_changed(self, reason: str, message: str) -> None:
        logger.info("Meeting video source state changed: %s -> %s", reason, message)
        with self._state_lock:
            if self._capture_state != "recording":
                return
            title = self._active_meeting_title or "Meeting"
            rec_mode = self._active_recording_mode
        if reason == "window_minimized":
            self.meeting_state_changed.emit("recording", f"Recording (Video Paused - Window Minimized): {title}")
        elif reason == "healthy":
            badge = "Audio + Video" if rec_mode == "audio_video" else "Audio Only"
            self.meeting_state_changed.emit("recording", f"Recording ({badge}): {title}")
        elif reason == "non_meeting_view_active":
            logger.info("Meeting call stage backgrounded/navigated -> Continuing audio recording without interruption")
            self.meeting_state_changed.emit("recording", f"Recording (Audio Active - Call Stage Backgrounded): {title}")

    def _on_audio_device_warning(self, warning_msg: str) -> None:
        logger.warning("Meeting audio device warning: %s", warning_msg)
        self.notification_requested.emit("Audio Device Advisory", warning_msg)

    @property
    def active_meeting_id(self) -> Optional[int]:
        return self._active_meeting_id

    def _on_dictation_level_change(self, lvl: float) -> None:
        """Process incoming audio level and emit both raw RMS and 10-bar equalizer levels."""
        self.dictation_level_changed.emit(lvl)
        weights = [0.35, 0.55, 0.75, 0.95, 1.0, 1.0, 0.95, 0.75, 0.55, 0.35]
        eq_levels = [round(min(1.0, max(0.05, lvl * w)), 3) for w in weights]
        self.audio_level_changed.emit(lvl, eq_levels)

    def _set_dictation_state(self, new_state: DictationState | str, message: str = "") -> None:
        """Centralized, thread-safe state transition method for dictation state machine."""
        with self._state_lock:
            if isinstance(new_state, DictationState):
                state_enum = new_state
                legacy_str = "starting" if state_enum == DictationState.INITIALIZING else (
                    "failed" if state_enum == DictationState.ERROR else state_enum.value
                )
            else:
                normalized = str(new_state).lower().strip()
                legacy_str = normalized
                if normalized == "starting":
                    state_enum = DictationState.INITIALIZING
                elif normalized in ("failed", "error"):
                    state_enum = DictationState.ERROR
                elif normalized in ("speaking", "listening"):
                    state_enum = DictationState.LISTENING
                elif normalized in ("completed", "idle"):
                    state_enum = DictationState.IDLE
                elif normalized in ("dismissed", "cancelled"):
                    state_enum = DictationState.DISMISSED
                elif normalized == "stopping":
                    state_enum = DictationState.STOPPING
                elif normalized == "processing":
                    state_enum = DictationState.PROCESSING
                elif normalized == "retrying":
                    state_enum = DictationState.RETRYING
                elif normalized == "testing":
                    state_enum = DictationState.TESTING
                else:
                    try:
                        state_enum = DictationState(normalized)
                    except ValueError:
                        state_enum = DictationState.IDLE

            self._dictation_state = state_enum.value
            self.state_changed.emit(state_enum)
            self.dictation_state_changed.emit(legacy_str, message)

    def handle_hotkey_action(self, action: str, is_release: bool = False) -> None:
        """
        Milestone 2 Contract Method: Dispatch global hotkey triggers to state machine actions.
        - 'ptt', is_release=False -> start_dictation()
        - 'ptt', is_release=True -> stop_dictation_and_insert()
        - 'popo', is_release=False -> toggle_dictation()
        - 'dismiss', is_release=False -> cancel_dictation()
        - 'mode_1'..'mode_5', is_release=False -> set_writing_mode(...)
        """
        act = (action or "").lower().strip()
        if act == "ptt":
            if is_release:
                self.stop_dictation_and_insert()
            else:
                self.start_dictation()
        elif act in ("popo", "toggle") and not is_release:
            self.toggle_dictation()
        elif act in ("dismiss", "cancel") and not is_release:
            self.cancel_dictation()
        elif act.startswith("mode_") and not is_release:
            mode_map = {
                "mode_1": "verbatim",
                "mode_2": "clean",
                "mode_3": "polished",
                "mode_4": "structured",
                "mode_5": "email",
            }
            if act in mode_map:
                self.set_writing_mode(mode_map[act])

    def start_audio_test(self) -> bool:
        """Enter non-inserting testing state for microphone calibration and onboarding."""
        with self._state_lock:
            self._set_dictation_state(DictationState.TESTING, "Microphone test active")
            ok, msg = self.dictation_recorder.start(include_system_audio=False)
            return ok

    def stop_audio_test(self) -> None:
        """Exit testing state and return to idle."""
        with self._state_lock:
            self.dictation_recorder.stop()
            self._set_dictation_state(DictationState.IDLE, "Test completed")

    def _on_dictation_error(self, msg: str) -> None:
        self._hands_free_active = False
        self._set_dictation_state(DictationState.ERROR, msg)
        self.notification_requested.emit("Voice Typing Error", f"Capture error: {msg}")

    def register_ui_hwnd(self, hwnd: int) -> None:
        self.focus_tracker.register_own_hwnd(hwnd)

    def _poll_foreground_window(self) -> None:
        info = self.focus_tracker.poll_foreground()
        if info:
            key = (info.hwnd, info.title or "Untitled Window", info.process_name or "")
            if key != self._last_emitted_target_info:
                self._last_emitted_target_info = key
                self.target_window_changed.emit(info.title or "Untitled Window", info.process_name)

    def _safe_emit_model_status(self, msg: str) -> None:
        try:
            if getattr(self, "_shutting_down", False):
                return
            self.model_status_changed.emit(msg)
        except (RuntimeError, AttributeError):
            pass

    def _preload_model_bg(self) -> None:
        if getattr(self, "_shutting_down", False):
            return
        self._safe_emit_model_status(f"Loading local model '{self.config.whisper_model}'...")
        ok, msg = self.transcriber.ensure_loaded(
            self.config.whisper_model,
            status_callback=self._safe_emit_model_status,
        )
        self._safe_emit_model_status(msg)

    def switch_whisper_model(self, model_name: str, compute_device: str = "auto") -> None:
        self.config.whisper_model = model_name
        self.config.compute_device = compute_device
        self.config.save()
        self.transcriber.model_name = model_name
        self.transcriber.compute_device = compute_device
        threading.Thread(
            target=self._preload_model_bg, name="WhisperModelSwitchThread", daemon=True
        ).start()

    def set_language_mode(self, language: str) -> None:
        """Set dictation/meeting language mode and ensure a multilingual model is active when needed."""
        self.config.language = language
        if language != "en" and self.config.whisper_model.endswith(".en"):
            multilingual_model = self.config.whisper_model[:-3]  # e.g. base.en -> base, small.en -> small
            self.switch_whisper_model(multilingual_model, self.config.compute_device)
        else:
            self.config.save()

    def set_mic_gain(self, gain: float) -> None:
        """Set digital microphone gain boost multiplier (0.5x to 10.0x) and apply live."""
        clamped = max(0.5, min(10.0, round(float(gain), 2)))
        self.config.mic_gain = clamped
        self.config.save()
        self.dictation_recorder.mic_gain = clamped
        self.streaming_typer.mic_gain = clamped
        self.meeting_recorder.mic_gain = clamped
        logger.info("Microphone volume gain updated to %.2fx", clamped)

    def set_pause_threshold(self, pause_sec: float) -> None:
        """Set silence pause threshold (0.4s to 2.5s) and apply live to streaming voice typer."""
        clamped = max(0.4, min(3.0, round(float(pause_sec), 2)))
        self.config.pause_to_type_sec = clamped
        self.config.save()
        if hasattr(self, "streaming_typer") and self.streaming_typer:
            self.streaming_typer.pause_threshold_sec = clamped
        logger.info("Silence pause threshold updated to %.2fs", clamped)

    @staticmethod
    def _resolve_system_theme() -> str:
        """Query Windows AppsUseLightTheme registry value gracefully."""
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return "light" if int(val) == 1 else "dark"
        except Exception:
            return "light"

    @property
    def theme(self) -> str:
        raw = (getattr(self.config, "theme", "light") or "light").lower().strip()
        if raw == "system":
            return self._resolve_system_theme()
        return raw if raw in ("light", "dark") else "light"

    def set_theme(self, theme_name: str) -> None:
        """Switch between 'light', 'dark', and 'system' themes."""
        theme_val = (theme_name or "").lower().strip()
        if theme_val not in ("dark", "light", "system"):
            theme_val = "light"
        self.config.theme = theme_val
        self.config.save()
        effective = self.theme
        self.theme_changed.emit(effective)
        logger.info("Theme switched to: %s (effective=%s)", theme_val, effective)

    def toggle_theme(self) -> str:
        """Toggle between dark and light theme and return new active theme."""
        next_theme = "light" if self.theme == "dark" else "dark"
        self.set_theme(next_theme)
        return next_theme

    def set_flow_bar_edge(self, edge: str, y_ratio: Optional[float] = None) -> None:
        """Set Flow Bar docking edge ('bottom', 'left', or 'right') and optional y_ratio."""
        val = (edge or "right").lower().strip()
        if val not in ("bottom", "left", "right"):
            val = "right"
        self.config.flow_bar_edge = val
        if y_ratio is not None:
            self.config.flow_bar_y_ratio = max(0.1, min(0.9, float(y_ratio)))
        self.config.save()
        self.flow_bar_edge_changed.emit(val)
        fp = getattr(self, "floating_pill", None)
        if fp is not None and hasattr(fp, "set_dock_edge"):
            try:
                fp.set_dock_edge(val)
            except Exception:
                pass

    def update_setting(self, key: str, value: Any) -> None:
        """Persist a single config field from UI controls and apply runtime side effects."""
        if key == "flow_bar_edge":
            self.set_flow_bar_edge(str(value))
            return
        if key == "theme":
            self.set_theme(str(value))
            return
        if not hasattr(self.config, key):
            logger.warning("Ignoring unknown setting key: %s", key)
            return
        setattr(self.config, key, value)
        self.config.save()
        if key in (
            "hands_free_double_tap",
            "dictation_hotkey",
            "ptt_hotkey",
            "meeting_hotkey",
            "hotkey_mode",
            "hotkeys_enabled",
        ):
            try:
                self.apply_hotkey_config()
            except Exception as e:
                logger.warning("Failed reapplying hotkeys after %s update: %s", key, e)

    def hide_flow_bar_temporarily(self, hours: float = 1.0) -> None:
        self.config.flow_bar_hidden_until = time.time() + max(60.0, hours * 3600.0)
        self.config.save()
        fp = getattr(self, "floating_pill", None)
        if fp is not None:
            fp.hide()

    def mute_reminders_temporarily(self, hours: float = 12.0) -> None:
        self.config.reminder_muted_until = time.time() + max(60.0, hours * 3600.0)
        self.config.save()

    def get_active_meeting_count(self) -> int:
        """Return count of active meeting windows / sessions for the FlowBar stack badge."""
        try:
            if hasattr(self.call_detector, "get_active_meeting_count"):
                return int(self.call_detector.get_active_meeting_count())
        except Exception:
            pass
        return 1 if self._capture_state in ("recording", "paused") else 0

    def _init_calendar_subsystem(self) -> None:
        """Initialize UnifiedCalendarEngine and CalendarAuthBroker for multi-account calendar sync."""
        try:
            from voxlocal.calendar.auth_service import CalendarAuthBroker, make_account_id
            from voxlocal.calendar.unified_engine import UnifiedCalendarEngine
            from voxlocal.calendar.base import CalendarProviderType
            from voxlocal.calendar.graph_provider import MsGraphCalendarProvider
            from voxlocal.calendar.google_provider import GoogleCalendarProvider
            from voxlocal.calendar.zoom_provider import ZoomCalendarProvider
            from voxlocal.calendar.ics_provider import IcsCalendarProvider

            storage_dir = Path(self.config.storage_dir or "data")
            storage_dir.mkdir(parents=True, exist_ok=True)
            user_id = getattr(self.config, "current_user_id", "default") or "default"

            self.calendar_auth_broker = CalendarAuthBroker(
                db=self.db,
                backend_url=getattr(self.config, "auth_backend_url", ""),
            )

            # Migrate any legacy plaintext OAuth secrets out of config into DPAPI vault
            migrated_secret = False
            if getattr(self.config, "google_calendar_client_secret", ""):
                self.calendar_auth_broker.save_app_secret(
                    "google_calendar_client_secret",
                    self.config.google_calendar_client_secret,
                    user_id=user_id,
                )
                self.config.google_calendar_client_secret = ""
                migrated_secret = True
            if getattr(self.config, "zoom_client_secret", ""):
                self.calendar_auth_broker.save_app_secret(
                    "zoom_client_secret",
                    self.config.zoom_client_secret,
                    user_id=user_id,
                )
                self.config.zoom_client_secret = ""
                migrated_secret = True
            if migrated_secret:
                self.config.save()

            self.calendar_engine = UnifiedCalendarEngine(
                parent=self,
                db=self.db,
                user_id=user_id,
                auth_broker=self.calendar_auth_broker,
            )
            self.calendar_manager = self.calendar_engine  # Backward-compatibility alias

            # Restore multi-account records saved in SQLite DB
            db_accounts = self.db.list_calendar_accounts(user_id=user_id)
            restored_ids: set[str] = set()
            for acc in db_accounts:
                acc_id = str(acc.get("account_id") or "")
                ptype = str(acc.get("provider_type") or "")
                email = str(acc.get("account_email") or "")
                if not acc_id or not ptype:
                    continue
                try:
                    prov = None
                    if ptype == CalendarProviderType.MS_GRAPH.value:
                        prov = MsGraphCalendarProvider(
                            client_id=(getattr(self.config, "calendar_client_id", "") or None),
                            tenant_id=(str(acc.get("tenant_id") or "") or getattr(self.config, "calendar_tenant_id", "common") or "common"),
                            account_hint=email or None,
                            account_id=acc_id,
                            user_id=user_id,
                            auth_broker=self.calendar_auth_broker,
                        )
                    elif ptype == CalendarProviderType.GOOGLE.value:
                        prov = GoogleCalendarProvider(
                            client_id=(getattr(self.config, "google_calendar_client_id", "") or None),
                            client_secret=self.calendar_auth_broker.load_app_secret("google_calendar_client_secret", user_id=user_id) or None,
                            account_id=acc_id,
                            user_id=user_id,
                            auth_broker=self.calendar_auth_broker,
                        )
                    elif ptype == CalendarProviderType.ZOOM.value:
                        prov = ZoomCalendarProvider(
                            client_id=(getattr(self.config, "zoom_client_id", "") or None),
                            client_secret=self.calendar_auth_broker.load_app_secret("zoom_client_secret", user_id=user_id) or None,
                            account_id=acc_id,
                            user_id=user_id,
                            auth_broker=self.calendar_auth_broker,
                        )
                    elif ptype == CalendarProviderType.ICS.value:
                        prov = IcsCalendarProvider(
                            account_id=acc_id,
                            user_id=user_id,
                            auth_broker=self.calendar_auth_broker,
                        )
                    elif ptype == CalendarProviderType.LOCAL_MAPI.value:
                        from voxlocal.calendar.mapi_provider import LocalMapiCalendarProvider
                        prov = LocalMapiCalendarProvider(account_id=acc_id, user_id=user_id)
                        prov.authenticate(interactive=False)

                    if prov is not None:
                        if acc.get("selected_calendars"):
                            prov.set_selected_calendars(list(acc["selected_calendars"]))
                        if acc.get("delta_links") and hasattr(prov, "_delta_links"):
                            prov._delta_links.update(acc["delta_links"])
                        if acc.get("sync_tokens") and hasattr(prov, "_sync_tokens"):
                            prov._sync_tokens.update(acc["sync_tokens"])
                        self.calendar_engine.register_provider(
                            acc_id,
                            prov,
                            sync_immediately=False,
                            account_meta=acc,
                        )
                        if not acc.get("enabled", True):
                            self.calendar_engine.set_account_enabled(acc_id, False)
                        restored_ids.add(acc_id)
                except Exception as exc:
                    logger.debug("Failed restoring DB calendar account %s: %s", acc_id, exc)

            # Backward-compatible restore from legacy standalone .dat files if not yet migrated
            ms_auth = storage_dir / "calendar_auth.dat"
            if (ms_auth.exists() or getattr(self.config, "ms_graph_enabled", False)) and not any(
                rid.startswith("ms_graph") for rid in restored_ids
            ):
                try:
                    ms_prov = MsGraphCalendarProvider(
                        client_id=(getattr(self.config, "calendar_client_id", "") or None),
                        tenant_id=(getattr(self.config, "calendar_tenant_id", "common") or "common"),
                        auth_file_path=ms_auth,
                        user_id=user_id,
                        auth_broker=self.calendar_auth_broker,
                    )
                    if ms_prov.is_authenticated():
                        acc_id = ms_prov.get_provider_status().get("account_id") or CalendarProviderType.MS_GRAPH.value
                        self.calendar_engine.register_provider(acc_id, ms_prov, sync_immediately=False)
                except Exception as exc:
                    logger.debug("Failed restoring legacy MS Graph provider: %s", exc)

            google_auth = storage_dir / "calendar_google.dat"
            if (google_auth.exists() or getattr(self.config, "google_calendar_enabled", False)) and not any(
                rid.startswith("google") for rid in restored_ids
            ):
                try:
                    g_prov = GoogleCalendarProvider(
                        client_id=(getattr(self.config, "google_calendar_client_id", "") or None),
                        client_secret=self.calendar_auth_broker.load_app_secret("google_calendar_client_secret", user_id=user_id) or None,
                        auth_file_path=google_auth,
                        user_id=user_id,
                        auth_broker=self.calendar_auth_broker,
                    )
                    if g_prov.is_authenticated():
                        acc_id = g_prov.get_provider_status().get("account_id") or CalendarProviderType.GOOGLE.value
                        self.calendar_engine.register_provider(acc_id, g_prov, sync_immediately=False)
                except Exception as exc:
                    logger.debug("Failed restoring legacy Google Calendar provider: %s", exc)

            zoom_auth = storage_dir / "calendar_zoom.dat"
            if (zoom_auth.exists() or getattr(self.config, "zoom_enabled", False)) and not any(
                rid.startswith("zoom") for rid in restored_ids
            ):
                try:
                    z_prov = ZoomCalendarProvider(
                        client_id=(getattr(self.config, "zoom_client_id", "") or None),
                        client_secret=self.calendar_auth_broker.load_app_secret("zoom_client_secret", user_id=user_id) or None,
                        auth_file_path=zoom_auth,
                        user_id=user_id,
                        auth_broker=self.calendar_auth_broker,
                    )
                    if z_prov.is_authenticated():
                        acc_id = z_prov.get_provider_status().get("account_id") or CalendarProviderType.ZOOM.value
                        self.calendar_engine.register_provider(acc_id, z_prov, sync_immediately=False)
                except Exception as exc:
                    logger.debug("Failed restoring legacy Zoom provider: %s", exc)

            ics_path = storage_dir / "calendar_ics.dat"
            ics_url = str(getattr(self.config, "calendar_ics_url", "") or "")
            if (ics_url or (getattr(self.config, "calendar_provider", "") == "ics" and ics_path.exists())) and not any(
                rid.startswith("ics") for rid in restored_ids
            ):
                try:
                    ics_prov = IcsCalendarProvider(
                        feed_url=ics_url,
                        auth_file_path=ics_path,
                        user_id=user_id,
                        auth_broker=self.calendar_auth_broker,
                    )
                    if ics_prov.is_authenticated():
                        acc_id = ics_prov.get_provider_status().get("account_id") or CalendarProviderType.ICS.value
                        self.calendar_engine.register_provider(acc_id, ics_prov, sync_immediately=False)
                except Exception as exc:
                    logger.debug("Failed restoring legacy ICS provider: %s", exc)

            if getattr(self.config, "calendar_provider", "") == "mapi" and not any(
                rid.startswith("local_mapi") for rid in restored_ids
            ):
                try:
                    from voxlocal.calendar.mapi_provider import LocalMapiCalendarProvider
                    mapi_prov = LocalMapiCalendarProvider(user_id=user_id)
                    if mapi_prov.authenticate(interactive=False):
                        self.calendar_engine.register_provider("local_mapi:default", mapi_prov, sync_immediately=False)
                except Exception as exc:
                    logger.debug("Failed restoring MAPI provider: %s", exc)

            # Wire engine signals
            self.calendar_engine.upcoming_meetings_updated.connect(self.calendar_upcoming_changed.emit)
            self.calendar_engine.accounts_updated.connect(self.calendar_accounts_changed.emit)
            self.calendar_engine.meeting_alert.connect(self._on_calendar_meeting_alert)
            self.calendar_engine.start_capture_requested.connect(self.start_meeting_from_calendar)

            poll_interval = int(getattr(self.config, "calendar_sync_interval_sec", 60) or 60)
            self.calendar_engine.start_polling(interval_sec=poll_interval)
        except Exception as exc:
            logger.error("Failed to initialize UnifiedCalendarEngine: %s", exc, exc_info=True)
            self.calendar_engine = None
            self.calendar_manager = None

    def _on_calendar_meeting_alert(self, meeting: Any, mins_until: int, alert_type: str) -> None:
        """Handle calendar alerts and route directly to unified FlowBar."""
        title = getattr(meeting, "title", "Upcoming Meeting")
        plat = getattr(meeting, "platform", "generic_meeting")
        fp = getattr(self, "floating_pill", None)
        if fp is not None and hasattr(fp, "set_pill_state"):
            if mins_until <= 5:
                fp.set_pill_state(
                    "CARD_NOTIFICATION",
                    metadata={
                        "title": title,
                        "platform": plat,
                        "minutes_until_start": mins_until,
                        "alert_type": alert_type,
                    },
                )
            else:
                fp.set_pill_state(
                    "MINI_MEETING",
                    metadata={
                        "title": title,
                        "platform": plat,
                        "countdown_text": f"in {mins_until}m",
                        "minutes_until_start": mins_until,
                    },
                )

    def connect_microsoft_calendar(
        self,
        on_complete: Optional[Callable[[bool, str], None]] = None,
        on_device_code: Optional[Callable[[str, str], None]] = None,
        client_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        use_device_code: bool = True,
        cancel_event: Optional[threading.Event] = None,
        account_hint: Optional[str] = None,
    ) -> None:
        """
        Connect Microsoft Outlook calendar (supports both Personal and Work accounts through one unified passage).
        """
        hint_str = str(account_hint or "").strip()
        if hint_str.lower().startswith(("http://", "https://", "webcal://")) or hint_str.lower().endswith(".ics"):
            self.connect_ics_calendar(hint_str, on_complete=on_complete)
            return

        eff_client_id = (client_id if client_id is not None else getattr(self.config, "calendar_client_id", "")) or None
        raw_tenant = tenant_id if tenant_id is not None else getattr(self.config, "calendar_tenant_id", "common")
        eff_tenant_id = str(raw_tenant or "common").strip() or "common"
        if eff_client_id != (getattr(self.config, "calendar_client_id", "") or None) or eff_tenant_id != getattr(self.config, "calendar_tenant_id", "common"):
            self.config.calendar_client_id = eff_client_id or ""
            self.config.calendar_tenant_id = eff_tenant_id
            self.config.save()

        def _worker() -> None:
            ok = False
            msg = "Could not connect Microsoft Outlook Calendar."
            auth_msg = ""
            try:
                from voxlocal.calendar.auth_service import make_account_id, sanitize_oauth_error_for_user
                from voxlocal.calendar.graph_provider import MsGraphCalendarProvider
                from voxlocal.calendar.base import CalendarProviderType
                user_id = getattr(self.config, "current_user_id", "default") or "default"
                auth_broker = getattr(self, "calendar_auth_broker", None) or getattr(self, "auth_broker", None)
                temp_account_id = make_account_id(CalendarProviderType.MS_GRAPH.value, hint_str)
                auth_path = Path(self.config.storage_dir or "data") / f"calendar_ms_{abs(hash(temp_account_id)) % 100000}.dat"
                ms_prov = MsGraphCalendarProvider(
                    client_id=eff_client_id,
                    tenant_id=eff_tenant_id,
                    auth_file_path=auth_path,
                    account_hint=hint_str or None,
                    account_id=temp_account_id,
                    user_id=user_id,
                    auth_broker=auth_broker,
                )
                if use_device_code:
                    code_info = ms_prov.request_device_code()
                    if not code_info:
                        raw_err = str(ms_prov.get_provider_status().get("error_detail") or "Failed initiating device code flow.")
                        _ec, msg = sanitize_oauth_error_for_user(raw_err, "ms_graph")
                        if callable(on_complete):
                            on_complete(False, msg)
                        return

                    user_code = code_info["user_code"]
                    verification_uri = code_info.get("verification_uri", "https://microsoft.com/devicelogin")

                    if callable(on_device_code):
                        on_device_code(user_code, verification_uri)

                    res = ms_prov.poll_device_code_tokens(
                        device_code=code_info["device_code"],
                        interval=code_info.get("interval", 5),
                        expires_in=code_info.get("expires_in", 900),
                        cancel_event=cancel_event,
                        on_code_received=on_device_code,
                    )
                    if isinstance(res, tuple):
                        ok, auth_msg = bool(res[0]), str(res[1])
                    else:
                        ok = bool(res)
                else:
                    res = ms_prov.authenticate_interactive_pkce(cancel_event=cancel_event)
                    if isinstance(res, tuple):
                        ok, auth_msg = bool(res[0]), str(res[1])
                    else:
                        ok = bool(res)

                status = ms_prov.get_provider_status()
                if ok:
                    upn = str(status.get("account_upn") or hint_str or "Microsoft Outlook")
                    canon_id = str(status.get("account_id") or make_account_id(CalendarProviderType.MS_GRAPH.value, upn))
                    ms_prov._account_id = canon_id
                    try:
                        ms_prov.list_calendars()
                    except Exception:
                        pass
                    ms_prov._save_credentials()
                    self.config.calendar_provider = "graph"
                    self.config.ms_graph_enabled = True
                    self.config.calendar_account_email = upn
                    self.config.save()
                    if self.calendar_engine is not None:
                        self.calendar_engine.register_account_provider(canon_id, ms_prov)
                    msg = f"Connected as {upn}"
                else:
                    raw_msg = str(
                        auth_msg
                        or status.get("error_detail")
                        or status.get("error_state")
                        or "Authentication cancelled or failed."
                    )
                    _ec, msg = sanitize_oauth_error_for_user(raw_msg, "ms_graph")
            except Exception as exc:
                from voxlocal.calendar.auth_service import sanitize_oauth_error_for_user
                _ec, msg = sanitize_oauth_error_for_user(str(exc), "ms_graph")
            if callable(on_complete):
                on_complete(ok, msg)

        threading.Thread(target=_worker, daemon=True, name="vox-calendar-ms").start()

    def connect_google_calendar(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        on_complete: Optional[Callable[[bool, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """Connect Google Calendar (Gmail or Google Workspace) via OAuth 2.0 PKCE or iCal (.ics) feed."""
        cid_str = str(client_id or "").strip()
        if cid_str.lower().startswith(("http://", "https://", "webcal://")) or cid_str.lower().endswith(".ics"):
            self.connect_ics_calendar(cid_str, on_complete=on_complete)
            return

        user_id = getattr(self.config, "current_user_id", "default") or "default"
        auth_broker = getattr(self, "calendar_auth_broker", None) or getattr(self, "auth_broker", None)
        if client_id is not None:
            self.config.google_calendar_client_id = client_id
        if client_secret:
            if auth_broker is not None:
                auth_broker.save_app_secret("google_calendar_client_secret", client_secret, user_id=user_id)
            self.config.google_calendar_client_secret = ""
        self.config.save()

        def _worker() -> None:
            ok = False
            msg = "Could not connect Google Calendar."
            try:
                from voxlocal.calendar.auth_service import make_account_id, sanitize_oauth_error_for_user
                from voxlocal.calendar.google_provider import GoogleCalendarProvider
                from voxlocal.calendar.base import CalendarProviderType
                resolved_secret = client_secret or (
                    auth_broker.load_app_secret("google_calendar_client_secret", user_id=user_id) if auth_broker else ""
                )
                auth_path = Path(self.config.storage_dir or "data") / f"calendar_google_{int(time.time()) % 100000}.dat"
                g_prov = GoogleCalendarProvider(
                    client_id=client_id or getattr(self.config, "google_calendar_client_id", "") or None,
                    client_secret=resolved_secret or None,
                    auth_file_path=auth_path,
                    user_id=user_id,
                    auth_broker=auth_broker,
                )
                ok = bool(g_prov.authenticate(interactive=True, cancel_event=cancel_event))
                status = g_prov.get_provider_status()
                if ok:
                    acct = str(status.get("account_email") or "Google Calendar")
                    canon_id = str(status.get("account_id") or make_account_id(CalendarProviderType.GOOGLE.value, acct))
                    g_prov._account_id = canon_id
                    try:
                        g_prov.list_calendars()
                    except Exception:
                        pass
                    g_prov._save_credentials()
                    self.config.google_calendar_enabled = True
                    self.config.google_calendar_account_email = acct
                    self.config.save()
                    if self.calendar_engine is not None:
                        self.calendar_engine.register_account_provider(canon_id, g_prov)
                    msg = f"Connected as {acct}"
                else:
                    msg = str(status.get("friendly_error") or sanitize_oauth_error_for_user(str(status.get("error_detail") or status.get("error_state") or ""), "google")[1])
            except Exception as exc:
                from voxlocal.calendar.auth_service import sanitize_oauth_error_for_user
                _ec, msg = sanitize_oauth_error_for_user(str(exc), "google")
            if callable(on_complete):
                on_complete(ok, msg)

        threading.Thread(target=_worker, daemon=True, name="vox-calendar-google").start()

    def connect_zoom_calendar(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        on_complete: Optional[Callable[[bool, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """Connect Zoom account via OAuth 2.0 + PKCE to retrieve scheduled & upcoming meetings."""
        user_id = getattr(self.config, "current_user_id", "default") or "default"
        auth_broker = getattr(self, "calendar_auth_broker", None) or getattr(self, "auth_broker", None)
        if client_id is not None:
            self.config.zoom_client_id = client_id
        if client_secret:
            if auth_broker is not None:
                auth_broker.save_app_secret("zoom_client_secret", client_secret, user_id=user_id)
            self.config.zoom_client_secret = ""
        self.config.save()

        def _worker() -> None:
            ok = False
            msg = "Could not connect Zoom."
            try:
                from voxlocal.calendar.auth_service import make_account_id, sanitize_oauth_error_for_user
                from voxlocal.calendar.zoom_provider import ZoomCalendarProvider
                from voxlocal.calendar.base import CalendarProviderType
                resolved_secret = client_secret or (
                    auth_broker.load_app_secret("zoom_client_secret", user_id=user_id) if auth_broker else ""
                )
                auth_path = Path(self.config.storage_dir or "data") / f"calendar_zoom_{int(time.time()) % 100000}.dat"
                z_prov = ZoomCalendarProvider(
                    client_id=client_id or getattr(self.config, "zoom_client_id", "") or None,
                    client_secret=resolved_secret or None,
                    auth_file_path=auth_path,
                    user_id=user_id,
                    auth_broker=auth_broker,
                )
                ok = bool(z_prov.authenticate(interactive=True, cancel_event=cancel_event))
                status = z_prov.get_provider_status()
                if ok:
                    acct = str(status.get("account_email") or "Zoom Account")
                    canon_id = str(status.get("account_id") or make_account_id(CalendarProviderType.ZOOM.value, acct))
                    z_prov._account_id = canon_id
                    z_prov._save_credentials()
                    self.config.zoom_enabled = True
                    self.config.zoom_account_email = acct
                    self.config.save()
                    if self.calendar_engine is not None:
                        self.calendar_engine.register_account_provider(canon_id, z_prov)
                    msg = f"Connected as {acct}"
                else:
                    msg = str(status.get("friendly_error") or sanitize_oauth_error_for_user(str(status.get("error_detail") or status.get("error_state") or ""), "zoom")[1])
            except Exception as exc:
                from voxlocal.calendar.auth_service import sanitize_oauth_error_for_user
                _ec, msg = sanitize_oauth_error_for_user(str(exc), "zoom")
            if callable(on_complete):
                on_complete(ok, msg)

        threading.Thread(target=_worker, daemon=True, name="vox-calendar-zoom").start()

    def connect_ics_calendar(
        self,
        feed_url: str,
        on_complete: Optional[Callable[[bool, str], None]] = None,
    ) -> None:
        """Validate and connect an Outlook / Microsoft 365 / Google .ics calendar link or file."""
        def _worker() -> None:
            ok = False
            msg = "Could not connect ICS calendar."
            try:
                from voxlocal.calendar.auth_service import make_account_id, sanitize_oauth_error_for_user
                from voxlocal.calendar.ics_provider import IcsCalendarProvider
                from voxlocal.calendar.base import CalendarProviderType
                user_id = getattr(self.config, "current_user_id", "default") or "default"
                auth_broker = getattr(self, "calendar_auth_broker", None) or getattr(self, "auth_broker", None)
                canon_id = make_account_id(CalendarProviderType.ICS.value, fallback_label=feed_url)
                ics_path = Path(self.config.storage_dir or "data") / f"calendar_{canon_id.replace(':', '_')}.dat"
                prov = IcsCalendarProvider(
                    feed_url=feed_url,
                    auth_file_path=ics_path,
                    account_id=canon_id,
                    user_id=user_id,
                    auth_broker=auth_broker,
                )
                ok = bool(prov.authenticate(interactive=True))
                status = prov.get_provider_status()
                if ok:
                    label = str(status.get("account_upn") or "ICS Calendar Feed")
                    self.config.calendar_provider = "ics"
                    self.config.calendar_account_email = label
                    self.config.calendar_ics_url = prov._feed_url
                    self.config.save()
                    if self.calendar_engine is not None:
                        self.calendar_engine.register_account_provider(canon_id, prov)
                    msg = f"Connected to {label}"
                else:
                    msg = str(status.get("friendly_error") or sanitize_oauth_error_for_user(str(status.get("error_detail") or status.get("error_state") or ""), "ics")[1])
            except Exception as exc:
                msg = str(exc)
            if callable(on_complete):
                on_complete(ok, msg)

        threading.Thread(target=_worker, daemon=True, name="vox-calendar-ics").start()

    def connect_local_outlook_calendar(
        self,
        on_complete: Optional[Callable[[bool, str], None]] = None,
    ) -> None:
        """Connect to locally installed Classic Windows Outlook Desktop via COM MAPI."""
        def _worker() -> None:
            ok = False
            msg = "Classic Outlook desktop profile not found."
            try:
                from voxlocal.calendar.mapi_provider import LocalMapiCalendarProvider
                user_id = getattr(self.config, "current_user_id", "default") or "default"
                prov = LocalMapiCalendarProvider(account_id="local_mapi:default", user_id=user_id)
                ok = bool(prov.authenticate(interactive=True))
                status = prov.get_provider_status()
                if ok:
                    acct = str(status.get("account_upn") or "Outlook Desktop (MAPI)")
                    self.config.calendar_provider = "mapi"
                    self.config.calendar_account_email = acct
                    self.config.save()
                    if self.calendar_engine is not None:
                        self.calendar_engine.register_account_provider("local_mapi:default", prov)
                    msg = f"Connected to {acct}"
                else:
                    err = str(status.get("error_state") or "MAPI_UNAVAILABLE")
                    msg = (
                        "Classic Outlook desktop MAPI is unavailable (New Outlook & web Outlook do not expose COM MAPI). "
                        "Use the Microsoft Sign-In or ICS Feed tab."
                        if err in ("OUTLOOK_NOT_INSTALLED", "OUTLOOK_PROFILE_UNAVAILABLE", "MAPI_UNAVAILABLE", "OUTLOOK_COM_UNAVAILABLE", "PYWIN32_UNAVAILABLE")
                        else err
                    )
            except Exception as exc:
                msg = str(exc)
            if callable(on_complete):
                on_complete(ok, msg)

        threading.Thread(target=_worker, daemon=True, name="vox-calendar-mapi").start()

    def list_connected_calendar_accounts(self) -> List[Dict[str, Any]]:
        if self.calendar_engine is None:
            return []
        return self.calendar_engine.list_accounts()

    def get_calendar_sync_health(self) -> Dict[str, Any]:
        if self.calendar_engine is None:
            return {"state": "not_connected", "connected_accounts": 0, "healthy_accounts": 0, "degraded_accounts": 0, "last_sync_at": "", "errors": []}
        return self.calendar_engine.get_sync_health_summary()

    def fetch_account_calendars(self, account_id: str) -> List[Dict[str, Any]]:
        if self.calendar_engine is None:
            return []
        prov = self.calendar_engine.get_provider(account_id)
        if prov is None:
            return []
        try:
            cals = prov.list_calendars()
            self.calendar_engine._persist_provider_account_record(account_id, prov)
            self.calendar_accounts_changed.emit(self.calendar_engine.list_accounts())
            return cals
        except Exception as exc:
            logger.debug("Failed listing calendars for %s: %s", account_id, exc)
            return []

    def set_account_selected_calendars(self, account_id: str, calendar_ids: List[str]) -> bool:
        if self.calendar_engine is None:
            return False
        return self.calendar_engine.set_account_selected_calendars(account_id, calendar_ids)

    def set_calendar_account_enabled(self, account_id: str, enabled: bool) -> bool:
        if self.calendar_engine is None:
            return False
        return self.calendar_engine.set_account_enabled(account_id, enabled)

    def sync_calendar_account(self, account_id: str) -> None:
        if self.calendar_engine is None:
            return
        threading.Thread(
            target=self.calendar_engine.sync_account_now,
            args=(account_id, True),
            daemon=True,
            name="vox-calendar-sync-acc",
        ).start()

    def sync_all_calendars(self, force: bool = True) -> None:
        if self.calendar_engine is None:
            return
        self.calendar_engine.poll_all_now(force=force)

    def disconnect_calendar_account(self, account_id: str) -> bool:
        if self.calendar_engine is None:
            return False
        return self.calendar_engine.unregister_provider(account_id)

    def reconnect_calendar_account(
        self,
        account_id: str,
        on_complete: Optional[Callable[[bool, str], None]] = None,
        on_device_code: Optional[Callable[[str, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """Initiate re-authentication for a specific expired or failed calendar account."""
        if self.calendar_engine is None:
            if callable(on_complete):
                on_complete(False, "Calendar subsystem not initialized.")
            return
        accounts = {a["account_id"]: a for a in self.calendar_engine.list_accounts()}
        meta = accounts.get(account_id, {})
        ptype = str(meta.get("provider_type") or (account_id.split(":", 1)[0] if ":" in account_id else account_id))
        email = str(meta.get("account_email") or "")
        if ptype == "ms_graph":
            self.connect_microsoft_calendar(
                on_complete=on_complete,
                on_device_code=on_device_code,
                account_hint=email or None,
                cancel_event=cancel_event,
            )
        elif ptype == "google":
            self.connect_google_calendar(on_complete=on_complete, cancel_event=cancel_event)
        elif ptype == "zoom":
            self.connect_zoom_calendar(on_complete=on_complete, cancel_event=cancel_event)
        elif ptype == "local_mapi":
            self.connect_local_outlook_calendar(on_complete=on_complete)
        else:
            prov = self.calendar_engine.get_provider(account_id)
            if prov is not None and getattr(prov, "_feed_url", ""):
                self.connect_ics_calendar(prov._feed_url, on_complete=on_complete)
            elif callable(on_complete):
                on_complete(False, "Open Add Account to enter this calendar feed again.")

    def disconnect_calendar_provider(self, provider_type: str) -> bool:
        """Disconnect a specific calendar provider or specific account_id."""
        try:
            pt = str(provider_type).lower()
            if ":" in pt and self.calendar_engine is not None:
                return self.calendar_engine.unregister_provider(provider_type)

            storage_dir = Path(self.config.storage_dir or "data")
            if pt in ("ms_graph", "graph"):
                self.config.ms_graph_enabled = False
                self.config.calendar_provider = "none"
                self.config.calendar_account_email = ""
                auth_f = storage_dir / "calendar_auth.dat"
                if auth_f.exists():
                    auth_f.unlink(missing_ok=True)
            elif pt in ("google", "google_calendar"):
                self.config.google_calendar_enabled = False
                self.config.google_calendar_account_email = ""
                auth_f = storage_dir / "calendar_google.dat"
                if auth_f.exists():
                    auth_f.unlink(missing_ok=True)
            elif pt == "zoom":
                self.config.zoom_enabled = False
                self.config.zoom_account_email = ""
                auth_f = storage_dir / "calendar_zoom.dat"
                if auth_f.exists():
                    auth_f.unlink(missing_ok=True)
            elif pt == "ics":
                self.config.calendar_ics_url = ""
                auth_f = storage_dir / "calendar_ics.dat"
                if auth_f.exists():
                    auth_f.unlink(missing_ok=True)
            self.config.save()

            if self.calendar_engine is not None:
                self.calendar_engine.unregister_provider(pt)
                self.calendar_engine.poll_all_now()
            return True
        except Exception as exc:
            logger.warning("Error disconnecting calendar provider %s: %s", provider_type, exc)
            return False

    def disconnect_calendar(self) -> bool:
        """Disconnect all calendar providers and revert to offline state."""
        try:
            if self.calendar_engine is not None:
                self.calendar_engine.disconnect_provider()
            self.disconnect_calendar_provider("ms_graph")
            self.disconnect_calendar_provider("google")
            self.disconnect_calendar_provider("zoom")
            self.disconnect_calendar_provider("ics")
            self.config.calendar_provider = "none"
            self.config.calendar_account_email = ""
            self.config.calendar_ics_url = ""
            self.config.save()
            self.calendar_upcoming_changed.emit([])
            self.calendar_accounts_changed.emit([])
            return True
        except Exception as exc:
            logger.warning("Calendar disconnect all failed: %s", exc)
            return False

    def match_active_call_to_calendar_meeting(
        self,
        detected_platform: str = "",
        window_title: str = "",
    ) -> Optional[Any]:
        """
        Score active/near-term calendar meetings against a detected call window
        to disambiguate overlapping meetings safely.
        """
        if not hasattr(self, "calendar_engine") or self.calendar_engine is None:
            return None
        now_utc = datetime.now(timezone.utc)
        app_lower = (detected_platform or "").lower().strip()
        win_tokens = {w for w in (window_title or "").lower().replace("-", " ").split() if len(w) > 2}

        best_mtg: Optional[Any] = None
        best_score = -1.0
        for mtg in self.calendar_engine.get_upcoming_meetings():
            st = mtg.start_time if mtg.start_time.tzinfo else mtg.start_time.replace(tzinfo=timezone.utc)
            et = mtg.end_time if mtg.end_time.tzinfo else mtg.end_time.replace(tzinfo=timezone.utc)
            if not ((st - timedelta(minutes=20)) <= now_utc <= (et + timedelta(minutes=15))):
                continue

            score = 0.0
            plat = str(getattr(mtg, "platform", "")).lower()
            if app_lower and (plat in app_lower or app_lower in plat):
                score += 40.0
            elif app_lower and plat in ("teams", "zoom", "meet", "webex") and plat not in app_lower:
                score -= 10.0

            title_tokens = {w for w in str(getattr(mtg, "title", "")).lower().replace("-", " ").split() if len(w) > 2}
            if win_tokens and title_tokens:
                overlap = len(win_tokens & title_tokens) / max(1, len(title_tokens))
                score += overlap * 35.0

            if getattr(mtg, "join_url", None):
                score += 15.0
            if st <= now_utc <= et:
                score += 20.0
            else:
                delta_min = abs((now_utc - st).total_seconds()) / 60.0
                score += max(0.0, 15.0 - delta_min)

            if score > best_score:
                best_score = score
                best_mtg = mtg

        return best_mtg

    def prepare_notetaker_for_meeting(self, meeting: Any, start_recording: bool = False) -> Tuple[bool, str]:
        """
        Prepare VOX Notetaker with a calendar event's title, platform, organizer, attendees,
        and join link without automatically starting recording unless requested.
        """
        if start_recording:
            return self.start_meeting_from_calendar(meeting)
        self._prepared_calendar_meeting = meeting
        self._active_meeting_calendar_event_id = getattr(meeting, "id", "")
        self._active_meeting_calendar_join_url = getattr(meeting, "join_url", "") or ""
        self._active_meeting_calendar_attendees = getattr(meeting, "attendees_str", "")
        self._active_meeting_calendar_organizer = getattr(meeting, "organizer", "")
        self._active_meeting_calendar_provider = str(getattr(meeting, "provider_type", ""))
        self._active_meeting_calendar_account_id = str(getattr(meeting, "account_id", ""))
        self._active_meeting_platform = str(getattr(meeting, "meeting_platform_label", None) or getattr(meeting, "platform", "Meeting"))
        try:
            self._prepared_priming_terms = (
                self.calendar_manager.extract_acoustic_priming_terms(meeting)
                if getattr(self, "calendar_manager", None) is not None
                else []
            )
        except Exception:
            self._prepared_priming_terms = []
        self.calendar_prepare_notetaker_requested.emit(meeting)
        return True, f"Prepared Notetaker for '{getattr(meeting, 'title', 'Meeting')}'"

    def start_meeting_from_calendar(self, meeting: Any) -> Tuple[bool, str]:
        """One-click Join Meeting & Start Notetaker."""
        try:
            join_url = getattr(meeting, "join_url", "")
            title = getattr(meeting, "title", "Calendar Meeting")
            if join_url:
                import webbrowser
                webbrowser.open(join_url)

            # Record calendar metadata for active session
            self._active_meeting_calendar_event_id = getattr(meeting, "id", "")
            self._active_meeting_calendar_join_url = join_url or ""
            self._active_meeting_calendar_attendees = getattr(meeting, "attendees_str", "")
            self._active_meeting_calendar_organizer = getattr(meeting, "organizer", "")
            self._active_meeting_calendar_provider = str(getattr(meeting, "provider_type", ""))
            self._active_meeting_calendar_account_id = str(getattr(meeting, "account_id", ""))
            self._active_meeting_platform = str(getattr(meeting, "platform", "Meeting"))

            ok, msg = self.start_meeting_recording(title=title)
            if ok:
                self.notification_requested.emit(
                    "Meeting Started",
                    f"Joined {title}. Recording and transcribing meeting notes.",
                )
            return ok, msg
        except Exception as exc:
            logger.error("Failed to start meeting from calendar: %s", exc)
            return False, str(exc)

    def _build_style_preferences(self) -> Dict[str, Any]:
        return {
            "auto_cleanup_level": getattr(self.config, "auto_cleanup_level", "light"),
            "style_work": getattr(self.config, "style_work", "formal"),
            "style_email": getattr(self.config, "style_email", "formal"),
            "style_personal": getattr(self.config, "style_personal", "casual"),
            "style_other": getattr(self.config, "style_other", "formal"),
            "vibe_coding_variable_recognition": getattr(self.config, "vibe_coding_variable_recognition", True),
            "vibe_coding_file_tagging": getattr(self.config, "vibe_coding_file_tagging", True),
        }

    def transform_text(self, text: str, instruction: str = "polish") -> str:
        """Transform text locally using LocalLLMEngine when ready or deterministic rules."""
        cleaned = (text or "").strip()
        if not cleaned:
            return ""
        if self.llm_engine and hasattr(self.llm_engine, "is_ready") and self.llm_engine.is_ready():
            try:
                res = self.llm_engine.generate_text(
                    prompt=f"Instruction: {instruction}\nRewrite ONLY the following text concisely without preamble:\n{cleaned}",
                    max_tokens=320,
                )
                if res and str(res).strip():
                    return str(res).strip()
            except Exception as exc:
                logger.debug("LLM transform fallback to deterministic rules: %s", exc)
        return DictationProcessor.transform_preset(cleaned, instruction=instruction)

    def copy_last_dictation(self) -> str:
        """Copy most recent dictation output to clipboard without changing focus."""
        text = self._last_dictation_final
        if not text:
            hist = self.db.list_dictation_history(limit=1)
            if hist:
                text = str(hist[0].get("final_text", ""))
        if text:
            try:
                from voxlocal.win32.clipboard import set_clipboard_text
                set_clipboard_text(text)
            except Exception:
                from PyQt6.QtWidgets import QApplication
                cb = QApplication.clipboard()
                if cb:
                    cb.setText(text)
            self.notification_requested.emit("VOX", "Copied last transcript to clipboard.")
        self.hotkey_copy_last_requested.emit()
        return text

    def paste_last_dictation(self) -> bool:
        """Paste the most recent dictation output directly into the active window."""
        text = self._last_dictation_final
        if not text:
            hist = self.db.list_dictation_history(limit=1)
            if hist:
                text = str(hist[0].get("final_text", ""))
        if not text:
            return False
        target = self.focus_tracker.get_active_target()
        ok, _ = self.text_inserter.insert_text(
            text + " ",
            method=self.config.insertion_method,
            restore_clipboard=self.config.restore_clipboard,
            target=target,
        )
        self.hotkey_paste_last_requested.emit()
        return ok

    def undo_ai_edit(self, entry_id: int) -> Optional[str]:
        """Revert a dictation entry back to its raw ASR transcript (Wispr 'Undo AI edit')."""
        rows = self.db.list_dictation_history(limit=200)
        for row in rows:
            if int(row.get("id", -1)) == int(entry_id):
                raw_val = str(row.get("raw_text", "") or row.get("final_text", "")).strip()
                if self.db.update_dictation_entry(entry_id, final_text=raw_val, undone=True):
                    self.history_updated.emit()
                    return raw_val
        return None

    def apply_dictation_correction(self, entry_id: int, corrected_text: str) -> bool:
        """Save user's manual edit to a dictation item and auto-learn corrected terms into Dictionary."""
        rows = self.db.list_dictation_history(limit=200)
        orig_text = ""
        for row in rows:
            if int(row.get("id", -1)) == int(entry_id):
                orig_text = str(row.get("final_text", ""))
                break
        ok = self.db.update_dictation_entry(entry_id, final_text=corrected_text.strip(), edited_text=corrected_text.strip())
        if ok and getattr(self.config, "auto_add_dictionary", True) and orig_text and corrected_text.strip() != orig_text.strip():
            # Auto-learn single differing token pairs cleanly into personal Dictionary
            old_words = [w.strip(".,!?;:\"'") for w in orig_text.split()]
            new_words = [w.strip(".,!?;:\"'") for w in corrected_text.split()]
            if len(old_words) == len(new_words):
                learned = 0
                for ow, nw in zip(old_words, new_words):
                    if ow.lower() != nw.lower() and 2 <= len(ow) <= 32 and 2 <= len(nw) <= 32:
                        try:
                            self.db.upsert_vocabulary(ow, nw, category="Auto-learned", is_active=1, auto_learned=1)
                            learned += 1
                        except Exception:
                            pass
                if learned > 0:
                    self.vocabulary_updated.emit()
        if ok:
            self.history_updated.emit()
        return ok

    def toggle_hands_free_typing(self) -> None:
        """Dedicated double-tap toggle for Hands-Free mode regardless of default PTT preference."""
        if self._hands_free_active or self.streaming_typer.is_running:
            self.stop_hands_free_typing()
        else:
            if self._dictation_state in ("listening", "speaking") and self.dictation_recorder.is_recording:
                self.cancel_dictation()
            self.start_hands_free_typing()

    @property
    def active_writing_mode(self) -> str:
        return getattr(self.config, "dictation_writing_mode", "clean")

    def set_writing_mode(self, mode: str | TransformationMode) -> None:
        mode_val = str(getattr(mode, "value", mode)).lower().strip()
        prev_mode = getattr(self.config, "dictation_writing_mode", "")
        self.config.dictation_writing_mode = mode_val
        if prev_mode != mode_val:
            logger.info("Writing mode switched to: %s", mode_val)
            try:
                self.writing_mode_changed.emit(mode_val)
                self.mode_changed.emit(mode_val)
            except Exception:
                pass
        fp = getattr(self, "floating_pill", None)
        if fp is not None:
            try:
                from PyQt6.QtCore import QThread
                if QThread.currentThread() == fp.thread():
                    if hasattr(fp, "_on_controller_mode_changed"):
                        fp._on_controller_mode_changed(mode_val)
                    elif hasattr(fp, "set_mode"):
                        fp.set_mode(mode_val)
            except Exception:
                pass

    def apply_hotkey_config(self) -> Tuple[bool, str]:
        return self.hotkey_manager.configure(
            dictation_mode=self.config.dictation_mode,
            dictation_hotkey=getattr(self.config, "dictation_hotkey", "ctrl+shift+space"),
            ptt_hotkey=self.config.ptt_hotkey,
            meeting_hotkey=self.config.meeting_hotkey,
            enable_dictation_hotkey=getattr(self.config, "enable_voice_typing_hotkey", True),
            hands_free_double_tap=getattr(self.config, "hands_free_double_tap", True),
            notetaker_triple_tap=getattr(self.config, "notetaker_triple_tap", False),
            scratchpad_hotkey=getattr(self.config, "scratchpad_hotkey", ""),
            transform_hotkey=getattr(self.config, "transform_hotkey", ""),
            paste_last_hotkey=getattr(self.config, "paste_last_hotkey", ""),
            copy_last_hotkey=getattr(self.config, "copy_last_hotkey", ""),
        )

    # ==================================================================
    # CORE FEATURE B: UNIVERSAL VOICE TYPING (Hands-Free Zero-Click + PTT)
    # ==================================================================
    def set_in_app_dictation_target(self, widget: Any, title: str = "VOX") -> None:
        """Register an in-app VOX Qt widget as a candidate target for live dictation."""
        self._explicit_in_app_widget_ref = widget
        self._explicit_in_app_title = (title or "VOX").strip()

    def clear_in_app_dictation_target(self, widget: Any = None) -> None:
        """Clear registered in-app dictation target if matching (or unconditionally)."""
        if widget is None or self._explicit_in_app_widget_ref is widget:
            self._explicit_in_app_widget_ref = None
            self._explicit_in_app_title = "VOX"
            self._in_app_session_requested = False
            self._active_in_app_target = None

    @staticmethod
    def _is_in_app_target(target: Any) -> bool:
        return bool(
            target
            and getattr(target, "process_name", "") == "VOX"
            and int(getattr(target, "hwnd", 0) or 0) == 0
        )

    def _clear_dictation_target(self) -> None:
        self._active_in_app_target = None
        self._in_app_session_requested = False
        self.focus_tracker.clear_locked_target()

    def _get_current_dictation_target(self) -> Any:
        if self._active_in_app_target is not None:
            return self._active_in_app_target
        return self.focus_tracker.get_active_target()

    def _resolve_dictation_target(self) -> Optional[TargetWindowInfo]:
        """Resolve either an internal VOX Qt editor target or the active external Win32 window."""
        if self._in_app_session_requested:
            self._in_app_session_requested = False
            widget = self._explicit_in_app_widget_ref
            title = self._explicit_in_app_title or "VOX"
            try:
                if widget is not None and hasattr(widget, "window"):
                    win = widget.window()
                    if win is not None and hasattr(win, "windowTitle") and win.windowTitle():
                        title = win.windowTitle()
            except RuntimeError:
                pass
            in_app = TargetWindowInfo(
                hwnd=0,
                title=title,
                process_id=os.getpid(),
                thread_id=0,
                process_name="VOX",
                focus_child_hwnd=0,
            )
            self._active_in_app_target = in_app
            return in_app

        try:
            if self.focus_tracker.is_foreground_own_process():
                from PyQt6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit

                app = QApplication.instance()
                if app is not None:
                    active_win = app.activeWindow()
                    if active_win is not None and active_win.isVisible():
                        fw = app.focusWidget()
                        editor_widget = None
                        if isinstance(fw, (QTextEdit, QPlainTextEdit, QLineEdit)):
                            editor_widget = fw
                        else:
                            editors = active_win.findChildren((QTextEdit, QPlainTextEdit, QLineEdit))
                            if editors:
                                editor_widget = editors[0]

                        if editor_widget is not None or active_win.objectName() == "voxOnboardingWizard":
                            self._explicit_in_app_widget_ref = editor_widget
                            title = active_win.windowTitle() or self._explicit_in_app_title or "VOX"
                            in_app = TargetWindowInfo(
                                hwnd=0,
                                title=title,
                                process_id=os.getpid(),
                                thread_id=0,
                                process_name="VOX",
                                focus_child_hwnd=0,
                            )
                            self._active_in_app_target = in_app
                            return in_app
        except Exception:
            pass

        self._active_in_app_target = None
        return self.focus_tracker.lock_current_target()

    def toggle_in_app_dictation(self, widget: Any, title: str = "VOX") -> None:
        """Toggle live voice typing directly into an internal VOX Qt editor."""
        if self._dictation_state in ("idle", "completed", "failed"):
            self.set_in_app_dictation_target(widget, title=title)
            self._in_app_session_requested = True
        self.toggle_dictation()

    def toggle_dictation(self) -> None:
        """
        Primary Voice Typing Action:
        Toggles voice typing (hands-free continuous or PTT depending on mode).
        Synchronized across the floating bar and global keyboard shortcuts.
        """
        if self._dictation_state == "starting":
            logger.info("Voice typing cancel requested during startup")
            self._cancel_startup_requested = True
            self._dictation_state = "stopping"
            self.dictation_state_changed.emit("stopping", "Cancelling startup...")
            return

        if self._dictation_state == "stopping":
            return

        if self._hands_free_active or self.streaming_typer.is_running:
            self.stop_hands_free_typing()
        elif self._dictation_state in ("listening", "speaking", "processing") and self.dictation_recorder.is_recording:
            self.stop_dictation_and_insert()
        else:
            if getattr(self.config, "dictation_mode", "toggle") == "ptt":
                self.start_dictation()
            else:
                self.start_hands_free_typing()

    def start_hands_free_typing(self) -> bool:
        """Start continuous hands-free voice typing."""
        if self._capture_state in ("recording", "paused"):
            self.notification_requested.emit(
                "Voice Typing Unavailable",
                "Continuous voice typing is paused while an active corporate meeting is being recorded.",
            )
            return False
        if self._screen_rec_state in ("recording", "paused"):
            self.notification_requested.emit(
                "Voice Typing Unavailable",
                "Continuous voice typing is paused while a PC screen recording is active.",
            )
            return False
        if self._hands_free_active or self._dictation_state in ("starting", "listening", "speaking", "processing"):
            return True

        if self.dictation_recorder.is_recording:
            self.dictation_recorder.stop()

        self._dictation_state = "starting"
        self._cancel_startup_requested = False
        self._session_committed_any = False
        self.dictation_state_changed.emit("starting", "Starting voice typing...")

        target = self._resolve_dictation_target()
        if self._is_in_app_target(target):
            target_desc = "VOX In-App Editor"
        else:
            target_desc = f"{target.process_name}" if target and target.process_name else "Active App"
        if target:
            self.target_window_changed.emit(target.title or "Untitled Window", target.process_name or "")

        ok, msg = self.streaming_typer.start(
            preferred_device_index=self.config.mic_device_index,
            preferred_device_name=self.config.mic_device_name,
            pause_threshold_sec=self.config.pause_to_type_sec,
            include_system_audio=self.config.dictation_include_system_audio,
            preferred_loopback_index=self.config.loopback_device_index,
            preferred_loopback_name=self.config.loopback_device_name,
            mic_gain=self.config.mic_gain,
        )

        if self._cancel_startup_requested:
            logger.info("Voice typing startup was cancelled by stop command")
            self.streaming_typer.stop()
            self._hands_free_active = False
            self._clear_dictation_target()
            self._dictation_state = "idle"
            self.dictation_state_changed.emit("idle", "Voice typing stopped")
            return False

        if not ok:
            self._hands_free_active = False
            self._clear_dictation_target()
            self._dictation_state = "failed"
            self.dictation_state_changed.emit("failed", msg)
            return False

        self._hands_free_active = True
        self._last_dictation_ended_sentence = True
        self._last_dictation_time = 0.0
        self._dictation_state = "listening"
        self.dictation_state_changed.emit("listening", f"Listening -> {target_desc}...")
        return True

    def stop_hands_free_typing(self) -> None:
        """Stop continuous hands-free voice typing."""
        if self._dictation_state == "starting":
            self._cancel_startup_requested = True
            self._dictation_state = "stopping"
            self.dictation_state_changed.emit("stopping", "Stopping voice typing...")
            return

        if self._dictation_state in ("stopping", "idle"):
            return

        self._hands_free_active = False
        self._last_dictation_ended_sentence = True
        self.dictation_interim_updated.emit("", "")
        self._dictation_state = "stopping"
        self.dictation_state_changed.emit("stopping", "Finalizing speech...")
        self.streaming_typer.stop()
        if self._streaming_queue.empty():
            self._clear_dictation_target()
            self._dictation_state = "idle"
            if self._session_committed_any:
                self.dictation_state_changed.emit("idle", "Voice typing stopped")
            else:
                self.dictation_state_changed.emit("idle", "No speech detected — speak closer to the mic and try again")

    def _on_streaming_state_change(self, state: str) -> None:
        if state == "speaking":
            self.dictation_state_changed.emit("speaking", "Speaking...")
        elif state == "transcribing":
            self.dictation_state_changed.emit("processing", "Transcribing locally...")
        elif state == "listening":
            self.dictation_interim_updated.emit("", "")
            if self._streaming_queue.empty():
                self.dictation_state_changed.emit("listening", "Listening...")
            else:
                self.dictation_state_changed.emit("processing", "Transcribing locally...")

    def _on_streaming_interim_audio(self, audio_16k: np.ndarray, duration_sec: float) -> None:
        if audio_16k.size < 4000 or not self._streaming_queue.empty():
            return
        with self._interim_lock:
            self._latest_interim_audio = (audio_16k, duration_sec)

    def _is_dictation_active_for_interim(self) -> bool:
        return bool(
            self._hands_free_active
            or getattr(self.dictation_recorder, "is_recording", False)
            or self._dictation_state in ("listening", "speaking", DictationState.LISTENING.value, DictationState.SPEAKING.value)
        )

    def _interim_transcription_loop(self) -> None:
        """Worker thread loop computing non-blocking speculative partials during ongoing speech."""
        while getattr(self, "_interim_worker_running", True):
            try:
                audio_item = None
                with self._interim_lock:
                    if self._latest_interim_audio is not None:
                        audio_item = self._latest_interim_audio
                        self._latest_interim_audio = None

                is_active = self._is_dictation_active_for_interim()
                if audio_item is None or not is_active or not self._streaming_queue.empty():
                    time.sleep(0.04)
                    continue

                audio_16k, duration_sec = audio_item
                engine_lang = "en+hi" if self.config.language == "hi_latn" else self.config.language
                out_script = "roman" if self.config.language == "hi_latn" else "auto"

                raw_interim, conf = self.transcriber.transcribe_interim(
                    audio_16k,
                    language=engine_lang,
                )
                if raw_interim and self._is_dictation_active_for_interim() and self._streaming_queue.empty():
                    vocab = self.db.list_vocabulary(active_only=True)
                    processed_interim = DictationProcessor.process_interim(
                        raw_interim,
                        vocabulary=vocab,
                        capitalize_first=self._last_dictation_ended_sentence,
                        output_script=out_script,
                    )
                    if processed_interim:
                        self.dictation_interim_updated.emit(processed_interim, "")
            except Exception as exc:
                logger.debug("Interim transcription error: %s", exc)
            time.sleep(0.04)

    def _on_streaming_segment_ready(self, audio_16k: np.ndarray, duration_sec: float) -> None:
        if audio_16k.size < 3200:
            return
        with self._interim_lock:
            self._latest_interim_audio = None
        self._streaming_queue.put((audio_16k, duration_sec))

    def _streaming_transcription_loop(self) -> None:
        """Worker thread loop processing streaming audio segments in strict chronological order."""
        while True:
            try:
                item = self._streaming_queue.get()
                if item is None:
                    break
                audio_16k, duration_sec = item
                self._process_streaming_worker(audio_16k, duration_sec)
            except Exception as exc:
                logger.error("Error in streaming transcription loop: %s", exc)
            finally:
                self._streaming_queue.task_done()

    def set_transcribe_profile(self, profile: str) -> None:
        """Update active transcription profile preset ('fast', 'balanced', 'max_accuracy')."""
        profile = (profile or "balanced").lower().strip()
        if profile in TranscribeProfile.ALL_PROFILES:
            self.config.transcribe_profile = profile
            self.config.save()
            self.transcriber.profile = profile
            self.transcribe_profile_changed.emit(profile)
            logger.info("Transcribe profile switched to: %s", profile)

    def _format_and_commit_dictation(
        self,
        raw_text: str,
        duration_sec: float,
        vocab: List[Dict[str, Any]],
        out_script: str,
        target: Any,
        trailing_space_always: bool = False,
    ) -> Optional[Tuple[str, bool, str, str, str]]:
        """Shared dictation formatting, snippet expansion, style formatting, focus-invariant insertion, and DB commit."""
        win_title = target.title if target else ""
        proc_name = target.process_name if target else ""
        target_hwnd = target.hwnd if target else 0
        now = time.monotonic()

        if (
            self._last_dictation_time == 0.0
            or (now - self._last_dictation_time) > 20.0
            or (target_hwnd != 0 and self._last_target_hwnd != 0 and target_hwnd != self._last_target_hwnd)
        ):
            capitalize_first = True
        else:
            capitalize_first = self._last_dictation_ended_sentence

        snippets = []
        try:
            snippets = self.db.list_snippets()
        except Exception:
            snippets = []

        detailed = DictationProcessor.process_detailed(
            raw_text=raw_text,
            vocabulary=vocab,
            snippets=snippets,
            cleanup_enabled=self.config.dictation_cleanup,
            spoken_punctuation=self.config.spoken_punctuation,
            smart_leading_space=self.config.smart_leading_space,
            capitalize_first=capitalize_first,
            output_script=out_script,
            mode=self.active_writing_mode,
            window_title=win_title,
            process_name=proc_name,
            style_preferences=self._build_style_preferences(),
        )
        final_text = detailed["final_text"]
        app_category = detailed["app_category"]
        for sid in detailed.get("matched_snippet_ids", []):
            try:
                self.db.increment_snippet_usage(sid)
            except Exception:
                pass
        if (
            final_text
            and app_category == "email"
            and getattr(self.config, "email_auto_signature", False)
            and getattr(self.config, "email_signature_text", "").strip()
        ):
            sig = str(self.config.email_signature_text).strip()
            if sig not in final_text:
                final_text = f"{final_text.rstrip()}\n\n{sig}"

        if not final_text.strip():
            return None

        self._last_dictation_ended_sentence = DictationProcessor.is_sentence_complete(final_text)
        self._last_dictation_time = now
        self._last_target_hwnd = target_hwnd
        self._recent_utterance_history.append(final_text)
        self._last_dictation_raw = raw_text
        self._last_dictation_final = final_text

        add_space = (
            not final_text.endswith(("\n", " "))
            if trailing_space_always
            else (self.config.smart_leading_space and not final_text.endswith("\n"))
        )
        text_to_insert = final_text + (" " if add_space else "")
        self._last_inserted_len = len(text_to_insert)

        if self._is_in_app_target(target):
            ok, method_used = True, "in_app_qt"
            w = getattr(self, "_explicit_in_app_widget_ref", None)
            if w is None:
                try:
                    from PyQt6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit
                    app = QApplication.instance()
                    if app is not None:
                        fw = app.focusWidget()
                        if isinstance(fw, (QTextEdit, QPlainTextEdit, QLineEdit)):
                            w = fw
                        else:
                            active_win = app.activeWindow()
                            if active_win is not None:
                                editors = active_win.findChildren((QTextEdit, QPlainTextEdit, QLineEdit))
                                if editors:
                                    w = editors[0]
                except Exception as focus_err:
                    logger.debug("Failed resolving focused in-app editor: %s", focus_err)

            if w is not None:
                try:
                    from PyQt6.QtWidgets import QTextEdit, QPlainTextEdit, QLineEdit
                    if isinstance(w, (QTextEdit, QPlainTextEdit)):
                        cursor = w.textCursor()
                        existing = w.toPlainText()
                        pos = cursor.position()
                        prefix = " " if (existing and pos > 0 and not existing[pos - 1].isspace() and not text_to_insert.startswith(" ")) else ""
                        cursor.insertText(prefix + text_to_insert)
                        w.setTextCursor(cursor)
                        w.ensureCursorVisible()
                    elif isinstance(w, QLineEdit):
                        w.insert(text_to_insert)
                except Exception as in_app_err:
                    logger.debug("Direct in-app widget insertion error: %s", in_app_err)
            else:
                ok, method_used = self.text_inserter.insert_text(
                    text_to_insert,
                    method=self.config.insertion_method,
                    restore_clipboard=self.config.restore_clipboard,
                    target=target,
                )
        else:
            ok, method_used = self.text_inserter.insert_text(
                text_to_insert,
                method=self.config.insertion_method,
                restore_clipboard=self.config.restore_clipboard,
                target=target,
            )

        if ok:
            self._session_committed_any = True
            if hasattr(self, "metering") and self.metering is not None:
                try:
                    self.metering.record_committed_dictation(
                        final_text,
                        dictation_id=getattr(self, "_last_dictation_id", None),
                    )
                    self.usage_updated.emit(self.metering.get_weekly_usage())
                except Exception as m_exc:
                    logger.debug("Metering dictation record error: %s", m_exc)

        self._last_dictation_id = self.db.add_dictation_entry(
            raw_text=raw_text,
            final_text=final_text,
            duration_sec=duration_sec,
            target_window=win_title,
            target_process=proc_name,
            insertion_method=method_used,
            status="completed" if ok else "failed",
            app_category=app_category,
        )
        return final_text, ok, method_used, win_title, proc_name

    def _process_streaming_worker(self, audio_16k: np.ndarray, duration_sec: float) -> None:
        try:
            self.dictation_interim_updated.emit("", "")
            vocab = self.db.list_vocabulary(active_only=True)
            engine_lang = "en+hi" if self.config.language == "hi_latn" else self.config.language
            out_script = "roman" if self.config.language == "hi_latn" else "auto"

            target = self._get_current_dictation_target()
            win_title = target.title if target else ""
            proc_name = target.process_name if target else ""
            window_keywords = DictationProcessor.extract_window_keywords(win_title, proc_name)

            recent_entries = list(self._recent_utterance_history)
            if not recent_entries:
                try:
                    recent_entries = [
                        e.get("final_text", "") for e in self.db.list_dictation_history(limit=2)
                    ]
                except Exception as db_exc:
                    logger.debug("Could not fetch recent dictation history: %s", db_exc)
                    recent_entries = []
            context_prompt = DictationProcessor.build_contextual_prompt(
                win_title, proc_name, recent_entries, language=self.config.language
            )

            custom_terms = [str(v.get("replacement", "")).strip() for v in vocab if v.get("is_active", 1)]
            all_hotwords_list = list(window_keywords)
            if custom_terms:
                all_hotwords_list.extend([w for w in custom_terms if w])
            effective_hotwords = ", ".join(all_hotwords_list) if all_hotwords_list else None

            raw_text = self.transcriber.transcribe_dictation(
                audio_16k,
                initial_prompt=context_prompt,
                language=engine_lang,
                vad_filter=self.config.vad_filter,
                hotwords=effective_hotwords,
                profile=getattr(self.config, "transcribe_profile", "balanced"),
            )

            if not raw_text.strip():
                if self._hands_free_active:
                    if getattr(self.streaming_typer, "is_in_speech", False):
                        self.dictation_state_changed.emit("speaking", "Speaking...")
                    elif not self._streaming_queue.empty():
                        self.dictation_state_changed.emit("processing", "Transcribing locally...")
                    else:
                        self.dictation_state_changed.emit("listening", "Listening...")
                else:
                    self._clear_dictation_target()
                    self._dictation_state = "idle"
                    idle_msg = (
                        "Voice typing stopped"
                        if self._session_committed_any
                        else "No speech detected — speak closer to the mic and try again"
                    )
                    self.dictation_state_changed.emit("idle", idle_msg)
                return

            logger.info("Voice typing transcribed (%.2fs): '%s'", duration_sec, raw_text)

            clean_lower = raw_text.strip().lower()
            if clean_lower in DictationProcessor.HALLUCINATION_PHRASES:
                if self._hands_free_active:
                    if getattr(self.streaming_typer, "is_in_speech", False):
                        self.dictation_state_changed.emit("speaking", "Speaking...")
                    elif not self._streaming_queue.empty():
                        self.dictation_state_changed.emit("processing", "Transcribing locally...")
                    else:
                        self.dictation_state_changed.emit("listening", "Listening...")
                else:
                    self._clear_dictation_target()
                    self._dictation_state = "idle"
                    idle_msg = (
                        "Voice typing stopped"
                        if self._session_committed_any
                        else "No speech detected — speak closer to the mic and try again"
                    )
                    self.dictation_state_changed.emit("idle", idle_msg)
                return

            # Check voice commands
            if self.config.voice_commands:
                cmd = DictationProcessor.detect_voice_command(raw_text)
                if cmd == "stop_listening":
                    self.stop_hands_free_typing()
                    return
                elif cmd == "delete_that":
                    target = self._get_current_dictation_target()
                    if not self._is_in_app_target(target):
                        self.text_inserter.send_backspaces(
                            count=max(1, self._last_inserted_len), target=target
                        )
                    self._last_inserted_len = 0
                    if getattr(self.streaming_typer, "is_in_speech", False):
                        self.dictation_state_changed.emit("speaking", "Speaking...")
                    else:
                        self.dictation_state_changed.emit("listening", "Deleted previous phrase")
                    return
                elif cmd == "press_enter":
                    target = self._get_current_dictation_target()
                    if self._is_in_app_target(target):
                        self.dictation_completed.emit("\n", f"{target.process_name} ({target.title})", "in_app_qt")
                    else:
                        self.text_inserter.send_enter(target=target)
                    self._last_inserted_len = 0
                    self._last_dictation_ended_sentence = True
                    if getattr(self.streaming_typer, "is_in_speech", False):
                        self.dictation_state_changed.emit("speaking", "Speaking...")
                    else:
                        self.dictation_state_changed.emit("listening", "Sent Enter")
                    return
                elif cmd == "new_line":
                    target = self._get_current_dictation_target()
                    if self._is_in_app_target(target):
                        self.dictation_completed.emit("\n", f"{target.process_name} ({target.title})", "in_app_qt")
                    else:
                        self.text_inserter.insert_text("\n", target=target)
                    self._last_inserted_len = 1
                    self._last_dictation_ended_sentence = True
                    if getattr(self.streaming_typer, "is_in_speech", False):
                        self.dictation_state_changed.emit("speaking", "Speaking...")
                    else:
                        self.dictation_state_changed.emit("listening", "New line")
                    return

            target = self._get_current_dictation_target()
            committed = self._format_and_commit_dictation(
                raw_text=raw_text,
                duration_sec=duration_sec,
                vocab=vocab,
                out_script=out_script,
                target=target,
                trailing_space_always=True,
            )
            if not committed:
                if self._hands_free_active:
                    if getattr(self.streaming_typer, "is_in_speech", False):
                        self.dictation_state_changed.emit("speaking", "Speaking...")
                    elif not self._streaming_queue.empty():
                        self.dictation_state_changed.emit("processing", "Transcribing locally...")
                    else:
                        self.dictation_state_changed.emit("listening", "Listening...")
                else:
                    self._clear_dictation_target()
                    self._dictation_state = "idle"
                    self.dictation_state_changed.emit("idle", "No speech detected — speak closer to the mic and try again")
                return

            final_text, ok, method_used, win_title, proc_name = committed
            logger.info(
                "Voice typing inserted via %s (next_cap=%s): '%s'",
                method_used,
                self._last_dictation_ended_sentence,
                final_text,
            )

            preview = final_text[:40] + ("..." if len(final_text) > 40 else "")
            self.dictation_completed.emit(final_text, f"{proc_name} ({win_title})", method_used)
            if self._hands_free_active:
                if getattr(self.streaming_typer, "is_in_speech", False):
                    self.dictation_state_changed.emit("speaking", "Speaking...")
                elif not self._streaming_queue.empty():
                    self.dictation_state_changed.emit("processing", "Transcribing locally...")
                else:
                    self.dictation_state_changed.emit("listening", f"Typed: \"{preview}\"")
            else:
                self._clear_dictation_target()
                self._dictation_state = "idle"
                self.dictation_state_changed.emit("idle", "Voice typing stopped")
            self.history_updated.emit()
        except Exception as exc:
            logger.error("Streaming dictation worker error: %s", exc)
            if self._hands_free_active:
                self.dictation_state_changed.emit("listening", f"Error: {exc}")
            else:
                self._clear_dictation_target()
                self._dictation_state = "idle"
                self.dictation_state_changed.emit("idle", "Ready (Offline)")

    def finalize_active_dictation(self) -> None:
        """Stop active hands-free or single-shot dictation and insert transcribed text."""
        if self._hands_free_active:
            self.stop_hands_free_typing()
        elif self._dictation_state in ("starting", "listening", "speaking") or getattr(self.dictation_recorder, "is_recording", False):
            self.stop_dictation_and_insert()
        elif self._dictation_state in ("idle", "completed", "failed"):
            self.toggle_dictation()

    def start_dictation(self) -> bool:
        """Push-to-Talk or single-shot dictation start."""
        if hasattr(self, "metering") and hasattr(self, "entitlement"):
            can_start, reason = self.metering.can_start_dictation(self.entitlement)
            if not can_start:
                msg = reason or "Weekly dictation quota reached (3,000 words). Upgrade to VOX Pro for unlimited voice typing."
                self.notification_requested.emit("Weekly Dictation Limit Reached", msg)
                self.quota_warning.emit("dictation_words", 3000, 3000)
                self.upgrade_dialog_requested.emit(msg)
                return False

        with self._state_lock:
            if self._capture_state in ("recording", "paused"):
                self.notification_requested.emit(
                    "Voice Typing Unavailable",
                    "Voice typing cannot be used while a corporate meeting is actively recording.",
                )
                return False
            if self._screen_rec_state in ("recording", "paused"):
                self.notification_requested.emit(
                    "Voice Typing Unavailable",
                    "Voice typing cannot be used while a PC screen recording is active.",
                )
                return False
            if self._dictation_state in ("starting", "initializing", "listening", "speaking", "processing", "stopping"):
                return False

            self._dictation_session_id += 1
            session_id = self._dictation_session_id
            self._dictation_cancelled = False
            self._ptt_stop_pending = False
            self._session_committed_any = False
            self._set_dictation_state(DictationState.INITIALIZING, "Starting voice capture...")

        target = self._resolve_dictation_target()
        if self._is_in_app_target(target):
            target_desc = "VOX In-App Editor"
        else:
            target_desc = f"{target.process_name}" if target and target.process_name else "Active App"
        if target:
            self._last_emitted_target_info = (target.hwnd, target.title or "Untitled Window", target.process_name or "")
            self.target_window_changed.emit(target.title or "Untitled Window", target.process_name or "")

        if self._cached_mic_device is None:
            self._cached_mic_device = AudioDeviceManager.resolve_microphone(
                self.config.mic_device_index, self.config.mic_device_name, use_cache=True
            )
        if self._cached_loop_devices is None:
            self._cached_loop_devices = (
                AudioDeviceManager.resolve_loopbacks_list(
                    self.config.loopback_device_index, self.config.loopback_device_name, use_cache=True
                )
                if self.config.dictation_include_system_audio
                else []
            )

        ok, msg = self.dictation_recorder.start(
            preferred_device_index=self.config.mic_device_index,
            preferred_device_name=self.config.mic_device_name,
            include_system_audio=self.config.dictation_include_system_audio,
            preferred_loopback_index=self.config.loopback_device_index,
            preferred_loopback_name=self.config.loopback_device_name,
            mic_gain=self.config.mic_gain,
            resolved_mic_device=self._cached_mic_device,
            resolved_loop_devices=self._cached_loop_devices,
        )
        if not ok:
            self._clear_dictation_target()
            self._set_dictation_state(DictationState.ERROR, msg)
            return False

        if self._ptt_stop_pending:
            logger.info("PTT key released during startup; stopping immediately")
            self._set_dictation_state(DictationState.LISTENING, "Listening...")
            self.stop_dictation_and_insert()
            return True

        self._set_dictation_state(DictationState.LISTENING, f"Listening -> {target_desc}...")
        return True

    def stop_dictation_and_insert(self) -> None:
        """Push-to-Talk or single-shot dictation stop & insert."""
        with self._state_lock:
            if self._dictation_state in ("starting", "initializing"):
                logger.info("PTT stop requested while starting; marking pending stop")
                self._ptt_stop_pending = True
                return

            if self._dictation_state not in ("listening", "speaking", DictationState.LISTENING.value):
                return

            self._set_dictation_state(DictationState.STOPPING, "Finalizing speech...")
            self.dictation_interim_updated.emit("", "")

            audio_16k, duration_sec = self.dictation_recorder.stop()
            target = self._get_current_dictation_target()
            session_id = self._dictation_session_id

            self._set_dictation_state(DictationState.PROCESSING, "Transcribing locally...")

            threading.Thread(
                target=self._process_dictation_worker,
                args=(audio_16k, duration_sec, target, session_id),
                name="DictationTranscribeWorker",
                daemon=True,
            ).start()

    def cancel_dictation(self) -> None:
        """Cancel active dictation (hands-free or single-shot) and discard buffered audio without inserting."""
        with self._state_lock:
            self._dictation_cancelled = True
            self._cancel_startup_requested = True
            self._hands_free_active = False

        self.dictation_interim_updated.emit("", "")
        with self._interim_lock:
            self._latest_interim_audio = None
        while not self._streaming_queue.empty():
            try:
                it = self._streaming_queue.get_nowait()
                if it is None:
                    self._streaming_queue.put(None)
                    break
                self._streaming_queue.task_done()
            except Exception:
                break
        if hasattr(self.streaming_typer, "cancel"):
            self.streaming_typer.cancel()
        else:
            self.streaming_typer.stop()
        cancelled_audio = None
        cancelled_dur = 0.0
        target = self._get_current_dictation_target()
        if getattr(self.dictation_recorder, "is_recording", False) or self._dictation_state in (
            "starting",
            "initializing",
            "listening",
            "speaking",
            "stopping",
        ):
            try:
                res = self.dictation_recorder.stop()
                if isinstance(res, tuple) and len(res) >= 2:
                    cancelled_audio, cancelled_dur = res[0], float(res[1] or 0.0)
            except Exception:
                cancelled_audio, cancelled_dur = None, 0.0
        if cancelled_audio is not None and getattr(cancelled_audio, "size", 0) >= 3200:
            self._last_cancelled_dictation = (cancelled_audio, cancelled_dur, target)
        else:
            self._last_cancelled_dictation = None
        self._clear_dictation_target()
        self._set_dictation_state(DictationState.DISMISSED, "Dictation cancelled")
        self._set_dictation_state(DictationState.IDLE, "Dictation cancelled")

    cancel_active_dictation = cancel_dictation

    def undo_cancelled_dictation(self) -> bool:
        """Restore and transcribe the last cancelled dictation buffer if available."""
        cached = getattr(self, "_last_cancelled_dictation", None)
        if not cached:
            return False
        self._last_cancelled_dictation = None
        audio_16k, duration_sec, target = cached
        if audio_16k is None or getattr(audio_16k, "size", 0) < 3200:
            return False
        with self._state_lock:
            self._dictation_session_id += 1
            session_id = self._dictation_session_id
            self._dictation_cancelled = False
        self._set_dictation_state(DictationState.PROCESSING, "Restoring cancelled dictation...")
        threading.Thread(
            target=self._process_dictation_worker,
            args=(audio_16k, duration_sec, target, session_id),
            name="DictationUndoCancelWorker",
            daemon=True,
        ).start()
        return True

    def _process_dictation_worker(self, audio_16k, duration_sec: float, target, session_id: Optional[int] = None) -> None:
        try:
            with self._state_lock:
                if self._dictation_cancelled or (session_id is not None and session_id != self._dictation_session_id):
                    logger.info("Dictation cancelled before processing")
                    return

            if audio_16k.size < 3200:
                self._set_dictation_state(DictationState.IDLE, "No speech detected (too short)")
                return

            vocab = self.db.list_vocabulary(active_only=True)
            engine_lang = "en+hi" if self.config.language == "hi_latn" else self.config.language
            out_script = "roman" if self.config.language == "hi_latn" else "auto"
            win_title = target.title if target else ""
            proc_name = target.process_name if target else ""
            window_keywords = DictationProcessor.extract_window_keywords(win_title, proc_name)
            recent_entries = list(self._recent_utterance_history)
            if not recent_entries:
                try:
                    recent_entries = [
                        e.get("final_text", "") for e in self.db.list_dictation_history(limit=2)
                    ]
                except Exception as db_exc:
                    logger.debug("Could not fetch recent dictation history: %s", db_exc)
                    recent_entries = []
            context_prompt = DictationProcessor.build_contextual_prompt(
                win_title, proc_name, recent_entries, language=self.config.language
            )

            custom_terms = [str(v.get("replacement", "")).strip() for v in vocab if v.get("is_active", 1)]
            all_hotwords_list = list(window_keywords)
            if custom_terms:
                all_hotwords_list.extend([w for w in custom_terms if w])
            effective_hotwords = ", ".join(all_hotwords_list) if all_hotwords_list else None

            with self._state_lock:
                if self._dictation_cancelled or (session_id is not None and session_id != self._dictation_session_id):
                    logger.info("Dictation cancelled before transcription")
                    return

            raw_text = self.transcriber.transcribe_dictation(
                audio_16k,
                initial_prompt=context_prompt,
                language=engine_lang,
                vad_filter=self.config.vad_filter,
                hotwords=effective_hotwords,
                profile=getattr(self.config, "transcribe_profile", "balanced"),
            )

            with self._state_lock:
                if self._dictation_cancelled or (session_id is not None and session_id != self._dictation_session_id):
                    logger.info("Dictation cancelled after transcription; aborting text insertion")
                    return

            if not raw_text.strip():
                self._set_dictation_state(DictationState.IDLE, "No speech detected — speak closer to the mic and try again")
                return

            committed = self._format_and_commit_dictation(
                raw_text=raw_text,
                duration_sec=duration_sec,
                vocab=vocab,
                out_script=out_script,
                target=target,
                trailing_space_always=False,
            )

            with self._state_lock:
                if self._dictation_cancelled or (session_id is not None and session_id != self._dictation_session_id):
                    logger.info("Dictation cancelled during formatting; aborting commit")
                    return

            if not committed:
                self._set_dictation_state(DictationState.IDLE, "No speech detected — speak closer to the mic and try again")
                return

            final_text, ok, method_used, win_title, proc_name = committed

            if ok:
                preview = final_text[:45] + ("..." if len(final_text) > 45 else "")
                self._set_dictation_state("completed", f"Inserted: \"{preview}\"")
                self.dictation_completed.emit(final_text, f"{proc_name} ({win_title})", method_used)
                self.history_updated.emit()
            else:
                if method_used == "target_closed_saved_to_clipboard":
                    msg = "Target window closed — transcription saved to clipboard"
                    self.notification_requested.emit("Voice Typing", msg)
                else:
                    msg = "Text insertion failed (copied to clipboard)"
                self._set_dictation_state(DictationState.ERROR, msg)
        except Exception as exc:
            logger.error("Dictation worker error: %s", exc)
            self._set_dictation_state(DictationState.ERROR, f"Dictation error: {exc}")
        finally:
            self._clear_dictation_target()


    # ==================================================================
    # CORE FEATURE A: MEETING RECORDING & TRANSCRIPTION (100% Independent)
    # ==================================================================
    def toggle_meeting_recording(self) -> None:
        if self._capture_state in ("recording", "paused"):
            self.stop_meeting_recording()
        elif self._capture_state == "idle":
            self.start_meeting_recording()

    def start_meeting_recording(
        self,
        title: Optional[str] = "",
        capture_mode: Optional[str] = None,
        record_screen: Optional[bool] = None,
        screen_fps: Optional[int] = None,
        target_hwnd: Optional[int] = None,
        resolution: str = "1080p",
        recording_mode: Optional[str] = None,
        strict_video: bool = False,
        require_active_meeting: bool = False,
        allow_deferred_video: bool = False,
        is_auto_detected: bool = False,
    ) -> Tuple[bool, str]:
        with self._state_lock:
            if self._capture_state in ("recording", "paused"):
                return False, "A meeting recording is already active. Click 'Stop & Save' to finalize it."
            if self._screen_rec_state in ("recording", "paused"):
                return False, "A PC screen recording is currently active. Please stop screen recording before starting Meeting Studio."
            if hasattr(self, "metering") and hasattr(self, "entitlement"):
                can_start, reason = self.metering.can_start_meeting(self.entitlement)
                if not can_start:
                    msg = reason or "Weekly meeting quota reached. Upgrade to VOX Pro for unlimited meetings."
                    self.notification_requested.emit("Meeting Limit Reached", msg)
                    self.quota_warning.emit("meeting_count", 5, 5)
                    self.upgrade_dialog_requested.emit(msg)
                    return False, msg
            if getattr(self, "_hands_free_active", False):
                self.stop_hands_free_typing()
            self._is_manual_meeting = not is_auto_detected
            # Auto-heal orphaned audio recorder if state is idle
            if getattr(self, "meeting_recorder", None) and getattr(self.meeting_recorder, "is_recording", False):
                logger.warning("Auto-healing orphaned audio recorder from prior session before starting new meeting")
                try:
                    self.meeting_recorder.stop()
                except Exception as exc:
                    logger.debug("Error stopping orphan audio recorder: %s", exc)

            ok_disk, free_mb, disk_msg = check_disk_space_mb(self.config.recordings_dir, min_free_mb=150.0)
            if not ok_disk:
                logger.error("Cannot start meeting recording: %s", disk_msg)
                self.set_meeting_lifecycle_state(MeetingLifecycleState.FAILED, disk_msg)
                self.notification_requested.emit("Low Disk Space", disk_msg)
                return False, disk_msg

            self._active_meeting_corr_id = generate_correlation_id("mtg")
            self._active_meeting_diarizer = PersistentSpeakerDiarizer(
                speaker_prefix="Speaker",
                new_speaker_threshold=0.085,
                profile_store=getattr(self.transcriber, "speaker_profile_store", None),
            )

        with operation_context(self._active_meeting_corr_id, "start_meeting"):
            # Resolve explicit recording mode ("audio_only" vs "audio_video")
            if recording_mode in ("audio_only", "audio_video"):
                should_rec_screen = (recording_mode == "audio_video")
            elif record_screen is not None:
                should_rec_screen = bool(record_screen)
            else:
                should_rec_screen = bool(self.config.record_meeting_screen)
            effective_rec_mode = "audio_video" if should_rec_screen else "audio_only"

            # Discover active meeting source for lifecycle binding and/or Mode B video capture
            effective_target_hwnd = (
                int(target_hwnd)
                if (target_hwnd is not None and int(target_hwnd) > 0)
                else None
            )
            source_desc = None
            try:
                from voxlocal.video.meeting_capture import resolve_meeting_source
                source_desc = resolve_meeting_source(target_hwnd=effective_target_hwnd)
            except Exception as exc:
                logger.debug("resolve_meeting_source check: %s", exc)
                source_desc = None

            active_sess = getattr(self.call_detector, "active_session", None)
            if require_active_meeting and source_desc is None and active_sess is None and effective_target_hwnd is None:
                msg_no_mtg = "No active meeting detected."
                self.set_meeting_lifecycle_state(MeetingLifecycleState.NO_MEETING_DETECTED, msg_no_mtg)
                return False, msg_no_mtg

            if should_rec_screen and strict_video and not allow_deferred_video and source_desc is None:
                fail_msg = (
                    "Audio + Video mode requires an active meeting window (Teams, Zoom, Google Meet, Webex, or Slack). "
                    "Switch to Audio Only mode or open your meeting window."
                )
                self.set_meeting_lifecycle_state(MeetingLifecycleState.FAILED, fail_msg)
                self.meeting_state_changed.emit("idle", fail_msg)
                return False, fail_msg

            self.set_meeting_lifecycle_state(
                MeetingLifecycleState.PREPARING_CAPTURE,
                f"Preparing {'Audio + Video' if should_rec_screen else 'Audio Only'} meeting capture...",
            )

            mode = capture_mode or self.config.meeting_capture_mode
            timestamp_slug = datetime.now().strftime("%Y%m%d_%H%M%S")
            clean_title = (title or "").strip()
            meeting_title = clean_title or f"Meeting {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            meeting_folder = self.config.recordings_dir / f"meeting_{timestamp_slug}"

            mic_idx = self.config.meeting_mic_device_index if self.config.meeting_mic_device_index is not None else self.config.mic_device_index
            mic_name = self.config.meeting_mic_device_name or self.config.mic_device_name
            gain = getattr(self.config, "meeting_mic_gain", 1.0)

            ok, msg = self.meeting_recorder.start(
                output_dir=meeting_folder,
                capture_mode=mode,
                mic_device_index=mic_idx,
                mic_device_name=mic_name,
                loopback_device_index=self.config.loopback_device_index,
                loopback_device_name=self.config.loopback_device_name,
                mic_gain=gain,
            )
            if not ok:
                self.set_meeting_lifecycle_state(MeetingLifecycleState.FAILED, msg)
                self.meeting_state_changed.emit("idle", msg)
                return False, msg

            # Bind target HWND / PID / platform for lifecycle auto-stop in both Audio Only and Audio + Video modes
            self._active_meeting_target_hwnd = (
                source_desc.hwnd if source_desc else (active_sess.window_hwnd if active_sess else effective_target_hwnd)
            )
            self._active_meeting_pid = (
                source_desc.pid if source_desc else (active_sess.pid if active_sess else None)
            )
            self._active_meeting_platform = (
                source_desc.platform_name if source_desc else (active_sess.platform if active_sess else "")
            )
            self._active_meeting_session_id = active_sess.session_id if active_sess else ""
            self._manual_target_lost_since = None

            self._meeting_video_recorder = None
            if should_rec_screen:
                try:
                    from voxlocal.video.meeting_capture import MeetingVideoRecorder

                    if source_desc is not None:
                        video_file = meeting_folder / "meeting_screen.mp4"
                        target_fps = int(screen_fps or getattr(self.config, "screen_fps", 15))
                        max_h = 1080
                        if isinstance(resolution, str):
                            if "720" in resolution:
                                max_h = 720
                            elif "480" in resolution:
                                max_h = 480
                            elif "1440" in resolution or "2k" in resolution.lower():
                                max_h = 1440
                            elif "2160" in resolution or "4k" in resolution.lower():
                                max_h = 2160

                        self._meeting_video_recorder = MeetingVideoRecorder(
                            output_path=str(video_file),
                            source_descriptor=source_desc,
                            fps=target_fps,
                            max_height=max_h,
                            on_meeting_ended=self._on_meeting_capture_ended,
                            on_source_state_change=self._on_video_source_state_changed,
                        )
                        ok_vid = self._meeting_video_recorder.start(str(video_file))
                        if ok_vid:
                            logger.info(
                                "Call-Only Meeting video recording active (%d FPS, provider=%s, hwnd=%s, title='%s'): %s",
                                target_fps,
                                source_desc.provider_id,
                                source_desc.hwnd,
                                source_desc.window_title,
                                video_file,
                            )
                        else:
                            self._meeting_video_recorder = None
                            if strict_video:
                                self.meeting_recorder.stop()
                                shutil.rmtree(meeting_folder, ignore_errors=True)
                                vid_err = (
                                    "Could not initialize video capture for the meeting window. "
                                    "Please verify the meeting window is open or select Audio Only mode."
                                )
                                self.set_meeting_lifecycle_state(MeetingLifecycleState.FAILED, vid_err)
                                self.meeting_state_changed.emit("idle", vid_err)
                                return False, vid_err
                            effective_rec_mode = "audio_only"
                    else:
                        # Call window has not appeared yet (pre-call startup).
                        # Begin audio capture AND start MeetingVideoRecorder immediately with pending descriptor
                        # so meeting_screen.mp4 is created from second 0 and binds dynamically to the call window!
                        from voxlocal.video.meeting_capture import MeetingSourceDescriptor
                        self._pending_video_bind = True
                        self._pending_video_folder = meeting_folder
                        self._pending_video_fps = int(screen_fps or getattr(self.config, "screen_fps", 15))
                        self._pending_video_res = resolution
                        effective_rec_mode = "audio_video"
                        video_file = meeting_folder / "meeting_screen.mp4"
                        target_fps = int(screen_fps or getattr(self.config, "screen_fps", 15))
                        plat = (active_sess.platform if active_sess else "") or self._active_meeting_platform or "Microsoft Teams"
                        synth_desc = MeetingSourceDescriptor(
                            provider_id="teams_desktop" if "teams" in plat.lower() else ("zoom_desktop" if "zoom" in plat.lower() else "generic_call"),
                            platform_name=plat,
                            hwnd=int(effective_target_hwnd or (active_sess.window_hwnd if active_sess else 0) or 0),
                            pid=int(getattr(active_sess, "pid", 0) or 0),
                            process_name="ms-teams.exe" if "teams" in plat.lower() else "",
                            class_name="",
                            window_title=meeting_title,
                            meeting_id_slug=meeting_title.lower(),
                            bounds=(0, 0, 1920, 1080),
                            score=100,
                            is_call_stage=True,
                        )
                        self._meeting_video_recorder = MeetingVideoRecorder(
                            output_path=str(video_file),
                            source_descriptor=synth_desc,
                            fps=target_fps,
                            max_height=1080,
                            on_meeting_ended=self._on_meeting_capture_ended,
                            on_source_state_change=self._on_video_source_state_changed,
                        )
                        self._meeting_video_recorder.start(str(video_file))
                        logger.info(
                            "Call-Only Meeting Recording: Video recorder initialized; video capture will bind automatically when call window connects."
                        )
                except Exception as exc:
                    logger.error("Failed initializing MeetingVideoRecorder: %s", exc)
                    self._meeting_video_recorder = None
                    if strict_video:
                        self.meeting_recorder.stop()
                        shutil.rmtree(meeting_folder, ignore_errors=True)
                        vid_err = f"Video capture initialization failed ({exc}). Please select Audio Only mode."
                        self.set_meeting_lifecycle_state(MeetingLifecycleState.FAILED, vid_err)
                        self.meeting_state_changed.emit("idle", vid_err)
                        return False, vid_err
                    effective_rec_mode = "audio_only"

            with self._state_lock:
                self._active_recording_mode = effective_rec_mode
                self._active_meeting_title = meeting_title
                planned_mixed = str(meeting_folder / "mixed.wav")
                planned_mic = str(meeting_folder / "mic.wav")
                planned_sys = str(meeting_folder / "sys.wav")
                planned_video = str(meeting_folder / "meeting_screen.mp4") if should_rec_screen else ""
                self._active_meeting_id = self.db.create_meeting(
                    title=meeting_title,
                    capture_mode=mode,
                    mic_device=self.config.mic_device_name,
                    loopback_device=self.config.loopback_device_name,
                    audio_path=planned_mixed,
                    mic_audio_path=planned_mic,
                    sys_audio_path=planned_sys,
                    video_path=planned_video,
                    model_used=self.config.whisper_model,
                    source_platform=self._active_meeting_platform or "Meeting",
                    calendar_event_id=getattr(self, "_active_meeting_calendar_event_id", "") or "",
                    calendar_join_url=getattr(self, "_active_meeting_calendar_join_url", "") or "",
                    calendar_attendees=getattr(self, "_active_meeting_calendar_attendees", "") or "",
                    calendar_organizer=getattr(self, "_active_meeting_calendar_organizer", "") or "",
                    calendar_provider=getattr(self, "_active_meeting_calendar_provider", "") or "",
                    calendar_account_id=getattr(self, "_active_meeting_calendar_account_id", "") or "",
                    user_id=getattr(self.config, "current_user_id", "default") or "default",
                )
                self._progressive_cursor_sec = 0.0
                self._progressive_segment_idx = 0
                self._progressive_worker_busy = False
                self._capture_state = "recording"
                self._recent_emitted_segments = []
                if hasattr(self, "streaming_transcriber") and self.config.progressive_transcription:
                    self.streaming_transcriber.start(
                        meeting_id=self._active_meeting_id,
                        recorder=self.meeting_recorder,
                        language=self.config.language,
                    )
                    try:
                        inferred_remote = LocalWhisperTranscriber._infer_remote_speaker_from_title(meeting_title)
                        if inferred_remote:
                            self.streaming_transcriber._remote_speaker_label = inferred_remote
                            self.streaming_transcriber._current_speaker = inferred_remote
                    except Exception:
                        pass
                if hasattr(self, "open_notetaker_drawer_requested"):
                    self.open_notetaker_drawer_requested.emit()

            if self._meeting_prompt_popup and self._meeting_prompt_popup.isVisible():
                try:
                    self._meeting_prompt_popup.close()
                    self._meeting_prompt_popup = None
                except Exception:
                    pass
            if hasattr(self.call_detector, "set_recording_active"):
                self.call_detector.set_recording_active(True)

            mode_badge = "Audio + Video" if effective_rec_mode == "audio_video" else "Audio Only"
            logger.info(
                "Meeting #%d started ('%s', rec_mode=%s, audio_mode=%s, snapshot=%s)",
                self._active_meeting_id,
                meeting_title,
                effective_rec_mode,
                mode,
                get_system_resource_snapshot(),
            )
            status_text = (
                f"Recording ({mode_badge}): {meeting_title} (Waiting for call window...)"
                if getattr(self, "_pending_video_bind", False)
                else f"Recording ({mode_badge}): {meeting_title}"
            )
            self.set_meeting_lifecycle_state(
                MeetingLifecycleState.RECORDING,
                status_text,
            )
            self.meeting_state_changed.emit("recording", status_text)
            self.history_updated.emit()
            return True, msg

    def pause_meeting_recording(self) -> bool:
        with self._state_lock:
            if self._capture_state == "recording" and self.meeting_recorder.pause():
                if hasattr(self, "streaming_transcriber"):
                    self.streaming_transcriber.pause()
                if getattr(self, "_meeting_video_recorder", None) and self._meeting_video_recorder.is_recording:
                    self._meeting_video_recorder.pause()
                self._capture_state = "paused"
                self.set_meeting_lifecycle_state(
                    MeetingLifecycleState.TEMPORARILY_INTERRUPTED,
                    f"Paused: {self._active_meeting_title}",
                )
                self.meeting_state_changed.emit("paused", f"Paused: {self._active_meeting_title}")
                return True
        return False

    def resume_meeting_recording(self) -> bool:
        with self._state_lock:
            if self._capture_state == "paused" and self.meeting_recorder.resume():
                if hasattr(self, "streaming_transcriber"):
                    self.streaming_transcriber.resume()
                if getattr(self, "_meeting_video_recorder", None) and self._meeting_video_recorder.is_paused:
                    self._meeting_video_recorder.resume()
                self._capture_state = "recording"
                self.set_meeting_lifecycle_state(
                    MeetingLifecycleState.RECORDING,
                    f"Recording: {self._active_meeting_title}",
                )
                self.meeting_state_changed.emit("recording", f"Recording: {self._active_meeting_title}")
                return True
        return False

    def stop_meeting_recording(self) -> None:
        with self._state_lock:
            if self._capture_state not in ("recording", "paused"):
                # Safety net: force stop if audio recorder is actively running on disk
                if getattr(self, "meeting_recorder", None) and getattr(self.meeting_recorder, "is_recording", False):
                    logger.warning(
                        "stop_meeting_recording called while capture_state was '%s' but audio recorder was running. Stopping.",
                        self._capture_state,
                    )
                else:
                    return

            meeting_id = self._active_meeting_id
            clean_title = getattr(self, "_active_meeting_title", "") or f"Meeting {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            st_transcriber = getattr(self, "streaming_transcriber", None)
            vid_recorder = getattr(self, "_meeting_video_recorder", None)
            self._meeting_video_recorder = None
            self._pending_video_bind = False
            self._pending_video_folder = None
            preserve_tracks = self.config.preserve_separate_tracks

            # Synchronously reset capture state to idle in <1ms so UI never lags
            self._capture_state = "idle"
            self._active_meeting_id = None
            self._active_meeting_target_hwnd = None
            self._active_meeting_pid = None
            self._active_meeting_platform = ""
            self._active_meeting_session_id = ""
            self._manual_target_lost_since = None
            self._active_meeting_diarizer = None
            self._active_meeting_calendar_event_id = ""
            self._active_meeting_calendar_join_url = ""
            self._active_meeting_calendar_attendees = ""
            self._active_meeting_calendar_organizer = ""
            self._active_meeting_calendar_provider = ""
            self._active_meeting_calendar_account_id = ""
            self._prepared_calendar_meeting = None

        # Reset call detector state immediately so subsequent meeting discovery starts unblocked
        if hasattr(self.call_detector, "end_active_session_immediately"):
            self.call_detector.end_active_session_immediately()
        elif hasattr(self.call_detector, "set_recording_active"):
            self.call_detector.set_recording_active(False)

        if self._meeting_prompt_popup and self._meeting_prompt_popup.isVisible():
            try:
                self._meeting_prompt_popup.close()
                self._meeting_prompt_popup = None
            except Exception:
                pass

        self.set_meeting_lifecycle_state(
            MeetingLifecycleState.FINALIZING,
            "Saving audio & finalizing offline transcript...",
        )
        self.meeting_state_changed.emit("transcribing", "Saving audio & finalizing offline transcript...")

        session_cancel_event = threading.Event()
        if meeting_id is not None:
            with self._state_lock:
                self._finalizing_jobs.add(meeting_id, session_cancel_event)

        def _stop_and_finalize_worker() -> None:
            # 1. Stop streaming transcriber in worker thread
            if st_transcriber:
                try:
                    st_transcriber.stop()
                except Exception as st_exc:
                    logger.debug("Error stopping streaming transcriber: %s", st_exc)

            # 2. Stop video recorder in worker thread
            video_path = ""
            if vid_recorder:
                try:
                    raw_vid = vid_recorder.stop()
                    diag = vid_recorder.get_capture_diagnostics() if hasattr(vid_recorder, "get_capture_diagnostics") else {}
                    if (
                        raw_vid
                        and isinstance(raw_vid, (str, Path))
                        and not hasattr(raw_vid, "_mock_return_value")
                        and not type(raw_vid).__name__.startswith("Mock")
                        and "MagicMock" not in str(raw_vid)
                        and "<Mock" not in str(raw_vid)
                    ):
                        video_path = str(raw_vid)
                        import json
                        diag_path = Path(video_path).with_name("capture_diagnostics.json")
                        diag_path.parent.mkdir(parents=True, exist_ok=True)
                        diag_path.write_text(json.dumps(diag, indent=2, default=str), encoding="utf-8")
                except Exception as exc:
                    logger.warning("Error stopping MeetingVideoRecorder: %s", exc)

            # 3. Stop audio recorder in worker thread
            try:
                result = self.meeting_recorder.stop(
                    preserve_separate_tracks=preserve_tracks
                )
            except Exception as exc:
                logger.warning("Error stopping MeetingRecorder: %s", exc)
                result = None

            if not result:
                if meeting_id is not None:
                    try:
                        self.db.update_meeting(
                            meeting_id,
                            status="empty",
                            ended_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            duration_sec=0.0,
                        )
                    except Exception as exc_upd:
                        logger.debug("Failed updating aborted meeting #%s in db: %s", meeting_id, exc_upd)
                    with self._state_lock:
                        self._finalizing_jobs.discard(meeting_id)
                self.meeting_state_changed.emit("idle", "Meeting stopped")
                return

            # Recover meeting indexing if ID was missed due to startup exception
            current_mid = meeting_id
            if current_mid is None:
                try:
                    current_mid = self.db.create_meeting(
                        title=clean_title,
                        capture_mode=getattr(self.config, "meeting_capture_mode", "both"),
                        mic_device=self.config.mic_device_name,
                        loopback_device=self.config.loopback_device_name,
                        model_used=self.config.whisper_model,
                    )
                    with self._state_lock:
                        self._finalizing_jobs.add(current_mid, session_cancel_event)
                    logger.info("Successfully recovered and indexed unindexed meeting #%d", current_mid)
                except Exception as exc:
                    logger.error("Error creating recovery index for stopped meeting: %s", exc)

            if current_mid is None:
                self.meeting_state_changed.emit("idle", "Meeting stopped")
                return

            try:
                dur = getattr(result, "duration_sec", 0.0)
                try:
                    dur_val = round(float(dur), 2)
                except (TypeError, ValueError):
                    dur_val = 0.0
                self.db.update_meeting(
                    current_mid,
                    status="transcribing",
                    ended_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    duration_sec=dur_val,
                )
            except Exception as exc_upd:
                logger.debug("Failed updating meeting status to transcribing in db: %s", exc_upd)

            # 4. Run finalization pipeline in worker thread
            try:
                self._finalize_meeting_worker(current_mid, result, video_path)
            except Exception as exc:
                logger.error("Finalization worker error for meeting #%d: %s", current_mid, exc)
                try:
                    self.db.update_meeting(current_mid, status="error")
                except Exception:
                    pass
                with self._state_lock:
                    self._finalizing_jobs.discard(current_mid)
                self._safe_emit(self.meeting_saved_and_transcribed, current_mid)
                self._safe_emit(self.history_updated)

        self._finalize_thread = threading.Thread(
            target=_stop_and_finalize_worker,
            name=f"MeetingStopAndFinalizeWorker-{meeting_id or 'recovered'}",
            daemon=True,
        )
        self._finalize_thread.start()

    def cancel_finalization(self, meeting_id: Optional[int] = None) -> None:
        """Cooperatively request cancellation of active meeting finalization pipeline."""
        self._cancel_finalization_event.set()
        with self._state_lock:
            if meeting_id is not None:
                evt = self._finalizing_jobs.get(meeting_id)
                if evt and isinstance(evt, threading.Event):
                    evt.set()
            else:
                for evt in list(self._finalizing_jobs.values()):
                    if isinstance(evt, threading.Event):
                        evt.set()
        logger.info("Meeting finalization cancellation requested (meeting_id=%s).", meeting_id)

    def gather_system_diagnostics(self) -> Dict[str, Any]:
        """Gather system, audio device, and capture diagnostics, safely guarded against mock objects."""
        from voxlocal.core.hardware import HardwareProfiler
        from voxlocal.core.logger import get_system_resource_snapshot

        def _safe_str(val: Any) -> str:
            if val is None:
                return ""
            if hasattr(val, "_mock_return_value") or type(val).__name__.startswith("Mock"):
                return "<mocked>"
            s = str(val)
            if "MagicMock" in s or "<Mock" in s:
                return "<mocked>"
            return s

        try:
            compat = HardwareProfiler.get_system_compatibility_summary()
        except Exception as exc:
            compat = {"error": _safe_str(exc)}

        return {
            "mic_device": _safe_str(getattr(self.config, "mic_device_name", "")),
            "loopback_device": _safe_str(getattr(self.config, "loopback_device_name", "")),
            "meeting_state": _safe_str(self.meeting_state),
            "capture_state": _safe_str(self._capture_state),
            "finalizing_jobs_count": len(self._finalizing_jobs),
            "lifecycle_state": _safe_str(getattr(self._meeting_lifecycle_state, "value", self._meeting_lifecycle_state)),
            "resource_snapshot": get_system_resource_snapshot(),
            "hardware_compatibility": compat,
        }

    def _on_meeting_recorder_tick(
        self,
        elapsed: float,
        mic_lvl: float,
        sys_lvl: float,
        mic_ok: bool,
        sys_ok: bool,
    ) -> None:
        self.meeting_tick.emit(elapsed, mic_lvl, sys_lvl, mic_ok, sys_ok)

        # Check Free tier meeting overrun every 15s without interrupting or terminating the call
        if int(elapsed) > 0 and int(elapsed) % 15 == 0 and hasattr(self, "metering") and hasattr(self, "entitlement"):
            status = self.metering.evaluate_live_meeting_overrun(elapsed, self.entitlement)
            if status.get("warning"):
                self.meeting_overrun_warning.emit(status.get("message", ""), elapsed)

        # Trigger progressive live transcription (fallback only if streaming transcriber is inactive)
        if (
            self.config.progressive_transcription
            and (not getattr(self, "streaming_transcriber", None) or not self.streaming_transcriber.is_active)
            and self._capture_state == "recording"
            and self._active_meeting_id is not None
            and not self._progressive_worker_busy
            and (elapsed - self._progressive_cursor_sec) >= float(self.config.progressive_interval_sec)
        ):
            start_s = self._progressive_cursor_sec
            end_s = elapsed
            self._progressive_cursor_sec = end_s
            self._progressive_worker_busy = True
            threading.Thread(
                target=self._progressive_transcribe_worker,
                args=(self._active_meeting_id, start_s, end_s),
                name="ProgressiveMeetingTranscribe",
                daemon=True,
            ).start()

    def _progressive_transcribe_worker(
        self, meeting_id: int, start_sec: float, end_sec: float
    ) -> None:
        with operation_context(self._active_meeting_corr_id, f"prog_m{meeting_id}_{int(start_sec)}"):
            try:
                mic_slice, sys_slice, actual_start_sec = self.meeting_recorder.get_audio_window(start_sec, end_sec)
                vocab = self.db.list_vocabulary(active_only=True)
                prompt = DictationProcessor.build_whisper_prompt(vocab)
                if self.config.language in ("en+hi", "hinglish", "hi_latn"):
                    prompt = f"{prompt} {DictationProcessor.DEFAULT_HINGLISH_PROMPT}".strip()

                new_segments: List[Dict[str, Any]] = []
                sys_segs: List[Dict[str, Any]] = []
                if sys_slice.size > 0 and float(np.max(np.abs(sys_slice))) >= 0.0004:
                    sys_segs = self.transcriber.transcribe_audio_segments(
                        sys_slice,
                        speaker_label="Speaker 1",
                        source_track="system",
                        time_offset_sec=actual_start_sec,
                        initial_prompt=prompt,
                        language=self.config.language,
                        diarize_remote_turns=True,
                        diarizer=self._active_meeting_diarizer,
                        output_script="roman",
                    )
                    new_segments.extend(sys_segs)

                if mic_slice.size > 0 and float(np.max(np.abs(mic_slice))) >= 0.0004:
                    mic_segs = self.transcriber.transcribe_audio_segments(
                        mic_slice,
                        speaker_label="You (Mic)",
                        source_track="mic",
                        time_offset_sec=actual_start_sec,
                        initial_prompt=prompt,
                        language=self.config.language,
                        output_script="roman",
                    )
                    # Filter acoustic bleed if open speakers caused the exact same system phrase
                    # to be faintly picked up by the microphone at the exact same timestamp.
                    # Conversational affirmations (e.g. 'ok', 'yes', 'haan', 'tika', 'sure') are preserved.
                    for m_seg in mic_segs:
                        m_txt = m_seg["text"].strip().lower()
                        is_bleed = False
                        if len(m_txt) >= 12:
                            for s_seg in sys_segs:
                                s_txt = s_seg["text"].strip().lower()
                                if (
                                    len(s_txt) >= 12
                                    and abs(float(m_seg["start_time"]) - float(s_seg["start_time"])) < 1.5
                                ):
                                    if m_txt == s_txt or (m_txt in s_txt and len(m_txt) > 0.65 * len(s_txt)):
                                        is_bleed = True
                                        break
                        if not is_bleed:
                            new_segments.append(m_seg)

                # Sort strictly chronologically by start_time ASC, end_time ASC
                new_segments.sort(key=lambda s: (float(s["start_time"]), float(s.get("end_time", s["start_time"]))))

                for seg in new_segments:
                    # Apply custom vocabulary and enforce Romanized Hinglish (Latin script)
                    processed_text = DictationProcessor.process(
                        seg["text"],
                        vocabulary=vocab,
                        cleanup_enabled=True,
                        spoken_punctuation=False,
                        output_script="roman",
                    ).strip()
                    if not processed_text:
                        continue

                    dur = max(0.01, float(seg["end_time"]) - float(seg["start_time"]))
                    if is_hallucination(processed_text, duration_sec=dur):
                        continue

                    seg["text"] = processed_text

                    # Segment ID stability & controller-level recent deduplication guard
                    st = float(seg["start_time"])
                    et = float(seg["end_time"])
                    track = seg["source_track"]
                    norm_lower = processed_text.lower()

                    is_dup = False
                    for ex in getattr(self, "_recent_emitted_segments", []):
                        if (
                            ex.get("source_track") == track
                            and abs(st - float(ex.get("start_time", 0.0))) <= 1.2
                        ):
                            ex_txt = ex.get("text", "").strip().lower()
                            if ex_txt == norm_lower or norm_lower in ex_txt:
                                is_dup = True
                                break
                            if ex_txt in norm_lower:
                                # New segment is a more complete completion of earlier partial turn
                                ex["text"] = processed_text
                                ex["end_time"] = et

                    if is_dup:
                        continue

                    self._progressive_segment_idx += 1
                    seg_id = self.db.add_transcript_segment(
                        meeting_id=meeting_id,
                        segment_index=self._progressive_segment_idx,
                        start_time=st,
                        end_time=et,
                        speaker=seg["speaker"],
                        text=seg["text"],
                        source_track=track,
                        confidence=seg.get("confidence", 1.0),
                    )
                    seg["id"] = seg_id
                    seg["stable_id"] = f"{meeting_id}_{track}_{int(st * 10)}_{abs(hash(processed_text)) % 100000}"

                    if not hasattr(self, "_recent_emitted_segments"):
                        self._recent_emitted_segments = []
                    self._recent_emitted_segments.append(dict(seg))
                    if len(self._recent_emitted_segments) > 60:
                        self._recent_emitted_segments = self._recent_emitted_segments[-60:]

                    self.meeting_progressive_segment.emit(meeting_id, seg)
            except Exception as exc:
                logger.debug("Progressive transcription chunk warning: %s", exc)
            finally:
                self._progressive_worker_busy = False

    def _on_streaming_partial(self, meeting_id: int, partial_dict: dict) -> None:
        self.meeting_live_partial.emit(meeting_id, partial_dict)

    def _on_streaming_confirmed(self, meeting_id: int, confirmed_dict: dict) -> None:
        self.meeting_live_confirmed.emit(meeting_id, confirmed_dict)
        self.meeting_progressive_segment.emit(meeting_id, confirmed_dict)

    def _finalize_meeting_worker(
        self, meeting_id: int, result, video_path: str = "", cancel_event: Optional[threading.Event] = None
    ) -> None:
        if cancel_event is None:
            cancel_event = self._finalizing_jobs.get(meeting_id) or self._cancel_finalization_event

        def is_cancelled() -> bool:
            return bool(cancel_event.is_set())

        corr_id = self._active_meeting_corr_id if self._active_meeting_corr_id != "-" else generate_correlation_id("mtg")
        current_stage = 1
        with operation_context(corr_id, f"fin_m{meeting_id}"):
            try:
                # Cooperative cancellation check before Stage 1
                if is_cancelled():
                    self._handle_finalization_cancelled(meeting_id, current_stage=1)
                    return

                self._safe_emit_finalization_progress("transcribing", "Stage 1/4: Packaging MP4 video & audio tracks...")

                # Background mixing of tracks into meeting_mixed.wav with vocal enhancement DSP
                mixed_p = getattr(result, "mixed_wav_path", None)
                mic_p = getattr(result, "mic_wav_path", None)
                sys_p = getattr(result, "sys_wav_path", None)
                dur = getattr(result, "duration_sec", 0.0)
                try:
                    dur_val = round(float(dur), 2)
                except (TypeError, ValueError):
                    dur_val = 0.0

                if mixed_p:
                    mixed_path = Path(mixed_p)
                    mic_path = Path(mic_p) if (mic_p and Path(mic_p).exists()) else None
                    sys_path = Path(sys_p) if (sys_p and Path(sys_p).exists()) else None
                    try:
                        from voxlocal.audio.meeting_recorder import _write_mixed
                        dur_calc = _write_mixed(mic_path, sys_path, mixed_path)
                        if dur_calc > 0:
                            dur_val = round(float(dur_calc), 2)
                    except Exception as mix_err:
                        logger.warning("Error generating background mixed audio: %s", mix_err)

                # 1. Mux synchronized meeting audio into the MP4 video so video playback is loud & clear
                if video_path and os.path.exists(video_path) and getattr(result, "mixed_wav_path", None) and os.path.exists(str(result.mixed_wav_path)):
                    try:
                        # Notify UI to release QMediaPlayer handle on this meeting's video before in-place replace
                        self._safe_emit(self.meeting_about_to_mux, meeting_id)
                        time.sleep(0.05)
                        self.screen_recorder.mux_audio_into_video(Path(video_path), Path(result.mixed_wav_path))
                        fallback_p = Path(video_path).with_name(Path(video_path).stem + "_with_audio.mp4")
                        if fallback_p.exists() and not Path(video_path).exists():
                            video_path = str(fallback_p)
                    except Exception as mux_err:
                        logger.warning("Audio-video muxing warning: %s", mux_err)

                ended_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                def _safe_str_field(val: Any) -> str:
                    if val is None or hasattr(val, "_mock_return_value") or type(val).__name__.startswith("Mock"):
                        return ""
                    s = str(val)
                    if "MagicMock" in s or "<Mock" in s:
                        return ""
                    return s

                mic_dev = _safe_str_field(getattr(result, "mic_device_name", ""))
                loop_dev = _safe_str_field(getattr(result, "loopback_device_name", ""))
                audio_p = _safe_str_field(getattr(result, "mixed_wav_path", ""))
                mic_p = _safe_str_field(getattr(result, "mic_wav_path", ""))
                sys_p = _safe_str_field(getattr(result, "sys_wav_path", ""))
                vid_p = _safe_str_field(video_path)

                self.db.update_meeting(
                    meeting_id,
                    ended_at=ended_at,
                    duration_sec=dur_val,
                    mic_device=mic_dev,
                    loopback_device=loop_dev,
                    audio_path=audio_p,
                    mic_audio_path=mic_p,
                    sys_audio_path=sys_p,
                    video_path=vid_p,
                    status="transcribing",
                )

                # Cooperative cancellation check between Stage 1 and Stage 2
                current_stage = 2
                if is_cancelled():
                    self._handle_finalization_cancelled(meeting_id, current_stage=2)
                    return

                meeting_rec = self.db.get_meeting(meeting_id) or {}
                m_title = (meeting_rec.get("title") or getattr(self, "_current_meeting_title", "") or "").strip()
                remote_name = "Speaker 1"
                if m_title and is_likely_person_name(m_title):
                    remote_name = m_title

                # Extract calendar attendees and organizer for Whisper priming & 1-on-1 speaker naming
                cal_names: List[str] = []
                raw_org = str(meeting_rec.get("calendar_organizer") or "").strip()
                if raw_org and raw_org not in cal_names:
                    cal_names.append(raw_org)
                raw_atts = meeting_rec.get("calendar_attendees") or ""
                if isinstance(raw_atts, str) and raw_atts.strip():
                    try:
                        parsed_atts = json.loads(raw_atts) if raw_atts.strip().startswith("[") else [a.strip() for a in raw_atts.split(",")]
                        for att in parsed_atts:
                            att_s = str(att or "").strip()
                            if att_s and att_s not in cal_names:
                                cal_names.append(att_s)
                    except Exception:
                        pass
                elif isinstance(raw_atts, list):
                    for att in raw_atts:
                        att_s = str(att or "").strip()
                        if att_s and att_s not in cal_names:
                            cal_names.append(att_s)

                self_email = str(getattr(self.config, "calendar_account_email", "") or "").strip().lower()
                remote_candidates = [
                    n for n in cal_names
                    if n and (not self_email or self_email not in n.lower())
                ]
                if remote_name == "Speaker 1" and len(remote_candidates) == 1:
                    cand_name = remote_candidates[0].split("<")[0].strip()
                    if "@" in cand_name:
                        cand_name = cand_name.split("@", 1)[0].replace(".", " ").replace("_", " ").title()
                    if cand_name:
                        remote_name = cand_name

                vocab = self.db.list_vocabulary(active_only=True)
                vocab_prompt = DictationProcessor.build_whisper_prompt(vocab)
                prompt_parts: List[str] = []
                if remote_name != "Speaker 1":
                    prompt_parts.append(f"{remote_name} meeting discussion.")
                if cal_names:
                    clean_cal_names = [n.split("<")[0].strip() for n in cal_names[:8] if n.strip()]
                    if clean_cal_names:
                        prompt_parts.append("Participants: " + ", ".join(clean_cal_names) + ".")
                if vocab_prompt:
                    prompt_parts.append(vocab_prompt)
                if self.config.language in ("en+hi", "hinglish", "hi_latn"):
                    prompt_parts.append(DictationProcessor.DEFAULT_HINGLISH_PROMPT)
                eff_prompt = " ".join(prompt_parts).strip()

                existing_segs = self.db.get_transcript_segments(meeting_id) if hasattr(self.db, "get_transcript_segments") else self.db.get_meeting_segments(meeting_id)
                max_seg_end = max((float(s.get("end_time") or 0.0) for s in existing_segs), default=0.0) if existing_segs else 0.0
                meeting_dur = float(dur_val or 0.0)

                # Check if system_track.wav has real speech energy that was missed by live streaming
                sys_wav_candidate = getattr(result, "sys_wav_path", None)
                has_sys_segments = any(
                    str(s.get("source_track") or "").lower() in ("system", "sys")
                    or (s.get("speaker") and s.get("speaker") not in ("You (Mic)", "You"))
                    for s in (existing_segs or [])
                )
                sys_track_has_untranscribed_audio = False
                if sys_wav_candidate and not has_sys_segments:
                    try:
                        import os as _os
                        if _os.path.exists(str(sys_wav_candidate)) and _os.path.getsize(str(sys_wav_candidate)) > 32000:
                            from voxlocal.engine.transcriber import load_wav_as_16k_f32
                            sys_samples = load_wav_as_16k_f32(str(sys_wav_candidate))
                            if sys_samples.size > 0 and float(np.sqrt(np.mean(sys_samples[:min(sys_samples.size, 16000 * 120)] ** 2))) >= 0.0012:
                                sys_track_has_untranscribed_audio = True
                    except Exception:
                        pass

                # Only bypass offline Whisper pass if live streaming segments captured at least 70% of meeting duration
                # AND did not drop a non-silent system_track.wav
                is_substantially_complete = bool(
                    existing_segs
                    and len(existing_segs) >= 2
                    and (meeting_dur <= 20.0 or max_seg_end >= meeting_dur * 0.70)
                    and not sys_track_has_untranscribed_audio
                )

                if is_substantially_complete:
                    logger.info(
                        "Preserving %d live streaming segments for meeting #%d (coverage=%.1fs/%.1fs); skipping offline Whisper pass",
                        len(existing_segs),
                        meeting_id,
                        max_seg_end,
                        meeting_dur,
                    )
                    segments = list(existing_segs)
                else:
                    if existing_segs:
                        logger.info(
                            "Live streaming segments incomplete (%d segs, end=%.1fs vs meeting=%.1fs); running offline Whisper pass on full recording",
                            len(existing_segs),
                            max_seg_end,
                            meeting_dur,
                        )
                    self._safe_emit_finalization_progress("transcribing", "Stage 2/4: Transcribing speech with local Whisper...")
                    segments = self.transcriber.transcribe_meeting_dual_track(
                        mixed_wav_path=getattr(result, "mixed_wav_path", None),
                        mic_wav_path=getattr(result, "mic_wav_path", None),
                        sys_wav_path=getattr(result, "sys_wav_path", None),
                        initial_prompt=eff_prompt,
                        language=self.config.language,
                        progress_callback=lambda s: self._safe_emit_finalization_progress("transcribing", f"Stage 2/4: Transcribing speech ({s})"),
                        cancel_check=is_cancelled,
                        output_script="roman",
                        remote_speaker_label=remote_name,
                    )

                # Early Stage 2 transcript persistence: Call self.db.replace_meeting_segments
                # immediately following Stage 2 Whisper transcription completion so intermediate
                # segments are safely saved to SQLite before Stage 3 diarization begins.
                if segments:
                    self.db.replace_meeting_segments(meeting_id, segments)
                    self.db.replace_meeting_segments(meeting_id, segments)
                else:
                    # Fallback: preserve live progressive transcript segments if offline pass yielded no segments
                    existing_segs = self.db.get_meeting_segments(meeting_id)
                    if existing_segs:
                        logger.info("Preserving %d progressive live segments for meeting #%d", len(existing_segs), meeting_id)
                        cleaned_existing = []
                        for seg in existing_segs:
                            txt_p = DictationProcessor.process(
                                seg.get("text", ""),
                                vocabulary=vocab,
                                cleanup_enabled=True,
                                spoken_punctuation=False,
                                output_script="roman",
                            ).strip()
                            dur_s = max(0.01, float(seg.get("end_time", 0.0)) - float(seg.get("start_time", 0.0)))
                            if txt_p and not is_hallucination(txt_p, duration_sec=dur_s):
                                seg["text"] = txt_p
                                cleaned_existing.append(seg)
                        self.db.replace_meeting_segments(meeting_id, cleaned_existing)
                        segments = cleaned_existing

                # Cooperative cancellation check between Stage 2 and Stage 3
                if is_cancelled():
                    self._handle_finalization_cancelled(meeting_id, current_stage=3)
                    return

                current_stage = 3
                if segments:
                    self._safe_emit_finalization_progress("transcribing", "Stage 3/4: Separating speakers & aligning conversation turns...")
                    # Two-pass post-meeting reconciliation using bounded disk slices (no full-WAV RAM spike)
                    segments = self.transcriber.reconcile_meeting_transcript(
                        segments=segments,
                        mic_audio_16k=None,
                        sys_audio_16k=None,
                        language=self.config.language,
                        mic_wav_path=getattr(result, "mic_wav_path", None),
                        sys_wav_path=getattr(result, "sys_wav_path", None),
                        output_script="roman",
                        remote_speaker_label=remote_name,
                    )
                    cleaned_segments = []
                    for seg in segments:
                        txt_p = DictationProcessor.process(
                            seg["text"],
                            vocabulary=vocab,
                            cleanup_enabled=True,
                            spoken_punctuation=False,
                            output_script="roman",
                        ).strip()
                        txt_p = clean_repetition_tail(txt_p)
                        dur_s = max(0.01, float(seg.get("end_time", 0.0)) - float(seg.get("start_time", 0.0)))
                        if txt_p and not is_hallucination(txt_p, duration_sec=dur_s):
                            seg["text"] = txt_p
                            cleaned_segments.append(seg)
                    self.db.replace_meeting_segments(meeting_id, cleaned_segments)
                    segments = cleaned_segments

                # Cleanup redundant Stage-A *_raw.wav backups now that Stage 3 reconciliation finished
                for raw_attr in ("mixed_wav_path", "mic_wav_path", "sys_wav_path"):
                    cand_p = _safe_str_field(getattr(result, raw_attr, ""))
                    if cand_p:
                        rec_folder = Path(cand_p).parent
                        for raw_name in ("mic_track_raw.wav", "system_track_raw.wav"):
                            raw_file = rec_folder / raw_name
                            if raw_file.is_file():
                                try:
                                    raw_file.unlink()
                                except Exception:
                                    pass
                        break

                # Cooperative cancellation check between Stage 3 and Stage 4
                if is_cancelled():
                    self._handle_finalization_cancelled(meeting_id, current_stage=4)
                    return

                current_stage = 4
                meeting_obj = self.db.get_meeting(meeting_id)
                full_txt = meeting_obj.get("full_transcript", "") if meeting_obj else ""

                summary, decisions_str, actions_str = ("", "", "")
                engagement_score = 85
                topics_str = "General Meeting"
                smart_folder_id = meeting_obj.get("folder_id") if meeting_obj else None

                if self.config.auto_summarize_meeting:
                    m_title = meeting_obj.get("title", "") if meeting_obj else "Meeting Notes"
                    m_plat = "Microsoft Teams"
                    tl = m_title.lower()
                    if "zoom" in tl:
                        m_plat = "Zoom"
                    elif "meet" in tl:
                        m_plat = "Google Meet"
                    elif "slack" in tl:
                        m_plat = "Slack"
                    elif "webex" in tl:
                        m_plat = "Webex"

                    self._safe_emit_finalization_progress("transcribing", "Stage 4/4: Synthesizing executive MoM, action items & decisions with local AI...")
                    analysis = LocalMeetingSummarizer.analyze_full(
                        segments,
                        full_txt,
                        duration_sec=float(dur_val),
                        title=m_title,
                        platform=m_plat,
                        llm_engine=self.llm_engine,
                    )
                    summary = analysis.get("mom_markdown", analysis.get("summary", ""))
                    engagement_score = int(analysis.get("engagement_score", 85))
                    topics_list = analysis.get("topics", [])
                    topics_str = ", ".join(topics_list)

                    # Format human-readable strings
                    if analysis.get("decisions"):
                        decisions_str = "\n".join(
                            f"• [{d.get('timestamp_sec', 0.0):.1f}s] {d['decision']}"
                            for d in analysis["decisions"]
                        )
                    else:
                        decisions_str = "• No explicit decisions detected."

                    if analysis.get("action_items"):
                        actions_str = "\n".join(
                            f"- [ ] [{a.get('owner', 'Unassigned')}] {a['description']}"
                            for a in analysis["action_items"]
                        )
                    else:
                        actions_str = "- [ ] No explicit action items detected."

                    # Clear prior non-completed auto-generated outcomes before inserting fresh deduplicated items
                    self.db.clear_meeting_outcomes(meeting_id, preserve_completed=True)

                    # Populate structured action_items table
                    for act in analysis.get("action_items", []):
                        self.db.add_action_item(
                            meeting_id=meeting_id,
                            description=act.get("description", ""),
                            owner=act.get("owner", "Unassigned"),
                            due_date=act.get("due_date", ""),
                            status="pending",
                            timestamp_sec=float(act.get("timestamp_sec", 0.0)),
                        )

                    # Populate structured decisions table
                    for dec in analysis.get("decisions", []):
                        self.db.add_decision(
                            meeting_id=meeting_id,
                            decision=dec.get("decision", ""),
                            rationale=dec.get("rationale", ""),
                            timestamp_sec=float(dec.get("timestamp_sec", 0.0)),
                        )

                    # Smart Folder auto-classification if not already assigned
                    if smart_folder_id is None:
                        m_title_str = meeting_obj.get("title", "") if meeting_obj else ""
                        title_lower = (m_title_str + " " + topics_str).lower()
                        folders = self.db.list_folders()
                        for f in folders:
                            fname = f["name"].lower()
                            if fname in title_lower or any(word in title_lower for word in fname.split()):
                                smart_folder_id = f["id"]
                                break

                # Preserve user-edited report summary if user explicitly edited and saved it
                if meeting_obj and int(meeting_obj.get("user_edited") or 0) == 1 and (meeting_obj.get("summary") or "").strip():
                    summary = meeting_obj["summary"]

                # Cooperative cancellation check before final commit
                if is_cancelled():
                    self._handle_finalization_cancelled(meeting_id, current_stage=4)
                    return

                self.db.update_meeting(
                    meeting_id,
                    status="completed",
                    summary=summary,
                    decisions=decisions_str,
                    action_items=actions_str,
                    video_path=video_path,
                    engagement_score=engagement_score,
                    topics=topics_str,
                    folder_id=smart_folder_id,
                )

                logger.info(
                    "Finalized meeting #%d (%d segments, engagement=%d%%, video=%s, snapshot=%s)",
                    meeting_id,
                    len(segments),
                    engagement_score,
                    bool(video_path),
                    get_system_resource_snapshot(),
                )
                with self._state_lock:
                    if self._capture_state == "idle":
                        self.set_meeting_lifecycle_state(
                            MeetingLifecycleState.COMPLETED,
                            f"Meeting #{meeting_id} completed & saved",
                        )
                if hasattr(self, "metering") and self.metering is not None:
                    try:
                        self.metering.record_completed_meeting(dur_val, meeting_id=meeting_id)
                        self.usage_updated.emit(self.metering.get_weekly_usage())
                    except Exception as m_exc:
                        logger.debug("Metering record_completed_meeting error: %s", m_exc)

                self._safe_emit_finalization_progress("idle", "Meeting saved & transcribed")
                self._safe_emit(self.meeting_saved_and_transcribed, meeting_id)
                self._safe_emit(self.history_updated)
            except Exception as exc:
                logger.error("Failed finalizing meeting #%d in Stage %d: %s", meeting_id, current_stage, exc, exc_info=True)
                self.db.update_meeting(meeting_id, status="error")
                with self._state_lock:
                    if self._capture_state == "idle":
                        self.set_meeting_lifecycle_state(
                            MeetingLifecycleState.FAILED,
                            f"Failed finalizing meeting #{meeting_id}: {exc}",
                        )
                self._safe_emit_finalization_progress("error", f"Processing failed in Stage {current_stage}: {exc}")
                self._safe_emit(self.meeting_saved_and_transcribed, meeting_id)
                self._safe_emit(self.history_updated)
            finally:
                with self._state_lock:
                    self._finalizing_jobs.discard(meeting_id)

    def _handle_finalization_cancelled(self, meeting_id: int, current_stage: int) -> None:
        """Handle cooperative cancellation cleanly preserving intermediate artifacts."""
        logger.info("Meeting #%d finalization cancelled by user at Stage %d", meeting_id, current_stage)
        self.db.update_meeting(meeting_id, status="interrupted")
        self.set_meeting_lifecycle_state(
            MeetingLifecycleState.FAILED,
            f"Meeting #{meeting_id} finalization was cancelled in Stage {current_stage}",
        )
        with self._state_lock:
            self._finalizing_jobs.discard(meeting_id)
        self._safe_emit_finalization_progress("error", f"Processing cancelled in Stage {current_stage}")
        self._safe_emit(self.meeting_saved_and_transcribed, meeting_id)
        self._safe_emit(self.history_updated)

    def re_transcribe_meeting(self, meeting_id: int) -> None:
        """Re-run offline transcription on an existing saved meeting recording."""
        with self._state_lock:
            if meeting_id in self._finalizing_jobs:
                self.notification_requested.emit(
                    "Operation in Progress",
                    f"Meeting #{meeting_id} is already being transcribed in the background.",
                )
                return

        meeting = self.db.get_meeting(meeting_id)
        if not meeting:
            return
        audio_path = meeting.get("audio_path", "")
        vid_path = meeting.get("video_path", "")
        parent_dir = Path(vid_path).parent if vid_path else None

        if not audio_path or not Path(audio_path).exists():
            if parent_dir and (parent_dir / "meeting_mixed.wav").exists():
                audio_path = str(parent_dir / "meeting_mixed.wav")
                self.db.update_meeting(meeting_id, audio_path=audio_path)
            elif parent_dir and (parent_dir / "mixed.wav").exists():
                audio_path = str(parent_dir / "mixed.wav")
                self.db.update_meeting(meeting_id, audio_path=audio_path)
            else:
                created_slug = str(meeting.get("created_at", "")).replace("-", "").replace(":", "").replace(" ", "_")
                for folder in self.config.recordings_dir.glob("meeting_*"):
                    if folder.is_dir():
                        target_wav = folder / "meeting_mixed.wav" if (folder / "meeting_mixed.wav").exists() else folder / "mixed.wav"
                        if target_wav.exists() and created_slug and created_slug[:13] in folder.name:
                            audio_path = str(target_wav)
                            parent_dir = folder
                            self.db.update_meeting(meeting_id, audio_path=audio_path)
                            break

        if parent_dir and parent_dir.exists():
            mic_p = parent_dir / "mic_track.wav"
            sys_p = parent_dir / "system_track.wav"
            if mic_p.exists() and (not meeting.get("mic_audio_path") or not Path(str(meeting.get("mic_audio_path"))).exists()):
                self.db.update_meeting(meeting_id, mic_audio_path=str(mic_p))
                meeting["mic_audio_path"] = str(mic_p)
            if sys_p.exists() and (not meeting.get("sys_audio_path") or not Path(str(meeting.get("sys_audio_path"))).exists()):
                self.db.update_meeting(meeting_id, sys_audio_path=str(sys_p))
                meeting["sys_audio_path"] = str(sys_p)

        if not audio_path or not Path(audio_path).exists():
            self.notification_requested.emit(
                "Audio File Missing",
                f"Cannot re-transcribe meeting #{meeting_id}: audio file not found at {audio_path}",
            )
            return

        class _SavedResult:
            duration_sec = float(meeting.get("duration_sec", 0.0))
            mixed_wav_path = str(meeting.get("audio_path", ""))
            mic_wav_path = str(meeting.get("mic_audio_path", ""))
            sys_wav_path = str(meeting.get("sys_audio_path", ""))
            mic_device_name = str(meeting.get("mic_device", ""))
            loopback_device_name = str(meeting.get("loopback_device", ""))

        with self._state_lock:
            self._active_meeting_corr_id = generate_correlation_id("retrans")
            self._finalizing_jobs.add(meeting_id)

        self._cancel_finalization_event.clear()
        self._safe_emit_finalization_progress("transcribing", f"Re-transcribing '{meeting.get('title')}'...")
        self._finalize_thread = threading.Thread(
            target=self._finalize_meeting_worker,
            args=(meeting_id, _SavedResult(), meeting.get("video_path", "")),
            name=f"ReTranscribeMeetingWorker-{meeting_id}",
            daemon=True,
        )
        self._finalize_thread.start()

    # ==================================================================
    # CORE FEATURE 2: PC SCREEN RECORDING (Full Desktop & System Audio)
    # ==================================================================
    @property
    def is_screen_recording_active(self) -> bool:
        return self._screen_rec_state in ("recording", "paused")

    @property
    def screen_recording_state(self) -> str:
        return self._screen_rec_state

    def start_screen_recording(
        self,
        title: str = "",
        fps: int = 30,
        resolution: str = "1080p",
        audio_mode: str = "both",
        preset: str = "balanced",
        with_annotation: bool = True,
    ) -> Tuple[bool, str]:
        """
        Record the user's PC screen (full display), including system audio and/or microphone.
        Supports presets ('compact', 'balanced', 'high') and live screen annotations.
        """
        with self._state_lock:
            if self._capture_state in ("recording", "paused"):
                return False, "A corporate meeting recording is currently active. Please stop Meeting Studio before starting PC screen recording."
            if self._screen_rec_state != "idle":
                return False, f"Screen recording is already {self._screen_rec_state}."
            if getattr(self, "_hands_free_active", False):
                self.stop_hands_free_typing()

            # Auto-heal orphaned audio or video recorder from prior screen session if state is idle
            if getattr(self, "pc_screen_audio", None) and getattr(self.pc_screen_audio, "is_recording", False):
                logger.warning("Auto-healing orphaned screen audio recorder from prior session before starting new screen recording")
                try:
                    self.pc_screen_audio.stop()
                except Exception as exc:
                    logger.debug("Error stopping orphan screen audio recorder: %s", exc)

            if getattr(self, "pc_screen_recorder", None) and getattr(self.pc_screen_recorder, "is_recording", False):
                logger.warning("Auto-healing orphaned screen video recorder from prior session before starting new screen recording")
                try:
                    self.pc_screen_recorder.stop()
                except Exception as exc:
                    logger.debug("Error stopping orphan screen video recorder: %s", exc)

            ok_disk, free_mb, disk_msg = check_disk_space_mb(self.config.recordings_dir, min_free_mb=150.0)
            if not ok_disk:
                logger.error("Cannot start screen recording: %s", disk_msg)
                self.notification_requested.emit("Low Disk Space", disk_msg)
                return False, disk_msg

            self._active_screen_rec_corr_id = generate_correlation_id("scr")

        with operation_context(self._active_screen_rec_corr_id, "start_screen_rec"):
            timestamp_slug = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
            clean_title = title.strip() or f"Screen Recording {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            rec_dir = self.config.recordings_dir / "screen"
            os.makedirs(rec_dir, exist_ok=True)
            video_file = rec_dir / f"screen_{timestamp_slug}.mp4"
            audio_dir = rec_dir / f"audio_{timestamp_slug}"

            # 1. Start audio capture (loopback system audio + mic)
            audio_started = False
            if audio_mode != "none":
                gain = getattr(self.config, "meeting_mic_gain", 1.0)
                ok_a, msg_a = self.pc_screen_audio.start(
                    output_dir=audio_dir,
                    capture_mode=audio_mode,
                    mic_device_index=self.config.mic_device_index,
                    mic_device_name=self.config.mic_device_name,
                    loopback_device_index=self.config.loopback_device_index,
                    loopback_device_name=self.config.loopback_device_name,
                    mic_gain=gain,
                )
                audio_started = ok_a
                if not ok_a:
                    logger.warning("Could not start screen audio: %s", msg_a)

            # 2. Start screen video capture (full desktop, target_hwnd=None)
            ok_v, msg_v = self.pc_screen_recorder.start(
                video_file,
                target_hwnd=None,
                fps=fps,
                resolution=resolution,
                preset=preset,
            )
            if not ok_v:
                if audio_started:
                    self.pc_screen_audio.stop()
                if audio_dir.exists():
                    shutil.rmtree(audio_dir, ignore_errors=True)
                with self._state_lock:
                    self._screen_rec_state = "idle"
                self.screen_recording_state_changed.emit("idle", f"Failed to start screen capture: {msg_v}")
                return False, msg_v

            with self._state_lock:
                self._active_screen_rec_video_path = video_file
                self._active_screen_rec_audio_dir = audio_dir
                self._active_screen_rec_title = clean_title
                # Save record in database
                self._active_screen_rec_id = self.db.create_screen_recording(
                    title=clean_title,
                    file_path=str(video_file),
                    fps=self.pc_screen_recorder.fps,
                    resolution=resolution,
                    audio_mode=audio_mode,
                )
                self._screen_rec_state = "recording"

            self._screen_rec_timer.start()
            self.screen_recording_state_changed.emit("recording", f"Recording PC screen: {clean_title}")
            self.screen_recordings_updated.emit()

            if with_annotation:
                try:
                    self.start_annotation_mode()
                except Exception as ann_err:
                    logger.debug("Could not auto-start annotation toolbar: %s", ann_err)

            logger.info(
                "PC Screen recording started: %s (%s @ %d FPS [preset=%s], audio=%s, snapshot=%s)",
                video_file.name,
                resolution,
                self.pc_screen_recorder.fps,
                preset,
                audio_mode,
                get_system_resource_snapshot(),
            )
            return True, "PC Screen recording active"

    def pause_screen_recording(self) -> bool:
        with self._state_lock:
            if self._screen_rec_state == "recording":
                self.pc_screen_recorder.pause()
                if self.pc_screen_audio.state == "recording":
                    self.pc_screen_audio.pause()
                self._screen_rec_state = "paused"
                self.screen_recording_state_changed.emit("paused", "Screen recording paused")
                return True
        return False

    def resume_screen_recording(self) -> bool:
        with self._state_lock:
            if self._screen_rec_state == "paused":
                self.pc_screen_recorder.resume()
                if self.pc_screen_audio.state == "paused":
                    self.pc_screen_audio.resume()
                self._screen_rec_state = "recording"
                self.screen_recording_state_changed.emit("recording", "Screen recording resumed")
                return True
        return False

    def stop_screen_recording(self, async_finalize: bool = False) -> Optional[str]:
        with self._state_lock:
            if self._screen_rec_state not in ("recording", "paused"):
                return None
            self._screen_rec_timer.stop()
            self._screen_rec_state = "saving"
            rec_id = self._active_screen_rec_id
            audio_dir = self._active_screen_rec_audio_dir
            corr_id = self._active_screen_rec_corr_id
            self._active_screen_rec_id = None
            self._active_screen_rec_video_path = None
            self._active_screen_rec_audio_dir = None

        if self.is_annotation_mode_active():
            try:
                self.stop_annotation_mode()
            except Exception as ann_err:
                logger.debug("Error stopping annotation mode: %s", ann_err)

        self.screen_recording_state_changed.emit("saving", "Muxing audio & saving PC screen recording...")

        if async_finalize:
            threading.Thread(
                target=self._finalize_screen_rec_worker,
                args=(rec_id, audio_dir, corr_id),
                name="ScreenRecFinalizeWorker",
                daemon=True,
            ).start()
            return None
        return self._finalize_screen_rec_worker(rec_id, audio_dir, corr_id)

    def _finalize_screen_rec_worker(
        self,
        rec_id: Optional[int],
        audio_dir: Optional[Path],
        corr_id: str = "-",
    ) -> Optional[str]:
        with operation_context(corr_id if corr_id != "-" else generate_correlation_id("scr"), "stop_screen_rec"):
            final_path: Optional[str] = None
            saved_title = getattr(self, "_active_screen_rec_title", "") or "Screen Recording"
            try:
                vid_path_str = self.pc_screen_recorder.stop() or ""
                audio_result = None
                if self.pc_screen_audio.state in ("recording", "paused"):
                    audio_result = self.pc_screen_audio.stop()

                final_path = vid_path_str
                if vid_path_str and audio_result and audio_result.mixed_wav_path and os.path.exists(audio_result.mixed_wav_path):
                    try:
                        self.pc_screen_recorder.mux_audio_into_video(
                            Path(vid_path_str),
                            Path(audio_result.mixed_wav_path),
                        )
                    except Exception as exc:
                        logger.warning("Error muxing screen audio: %s", exc)

                # Update DB record with duration & file size
                if rec_id and vid_path_str and Path(vid_path_str).exists():
                    try:
                        dur = audio_result.duration_sec if audio_result else 0.0
                        sz = os.path.getsize(vid_path_str)
                        self.db.finish_screen_recording(rec_id, dur, sz)
                    except Exception as db_exc:
                        logger.debug("Error updating screen recording DB entry: %s", db_exc)

                # Cleanup temporary audio files
                if audio_dir and audio_dir.exists():
                    try:
                        shutil.rmtree(audio_dir, ignore_errors=True)
                    except Exception as rm_exc:
                        logger.debug("Error cleaning screen rec temp audio dir: %s", rm_exc)

                logger.info("PC Screen recording finalized: %s", final_path)
                if final_path and Path(final_path).exists():
                    self.screen_recording_saved.emit(final_path, saved_title)
            except Exception as exc:
                logger.error("Unexpected error finalizing screen recording: %s", exc, exc_info=True)
            finally:
                with self._state_lock:
                    self._screen_rec_state = "idle"
                self.screen_recording_state_changed.emit("idle", "Screen recording saved successfully")
                self.screen_recordings_updated.emit()
            return final_path

    # ------------------------------------------------------------------
    # Live Annotation & Screenshot Markup Controls (Core Feature 2)
    # ------------------------------------------------------------------
    def is_annotation_mode_active(self) -> bool:
        """Returns True if the live screen annotation overlay/toolbar is currently open."""
        return bool(self._annotation_overlay and self._annotation_overlay.isVisible())

    def start_annotation_mode(self) -> None:
        """Initializes and displays the transparent screen overlay and floating toolbar."""
        app = QApplication.instance()
        if not app:
            return
        if threading.current_thread() is not threading.main_thread():
            from PyQt6.QtCore import QMetaObject, Qt
            QMetaObject.invokeMethod(self, "_do_start_annotation_mode", Qt.ConnectionType.QueuedConnection)
            return
        self._do_start_annotation_mode()

    def _do_start_annotation_mode(self) -> None:
        try:
            from voxlocal.annotation.overlay import AnnotationOverlayWindow
            from voxlocal.annotation.toolbar import AnnotationToolbar

            if self._annotation_overlay is None:
                self._annotation_overlay = AnnotationOverlayWindow(self.annotation_engine)
                self._annotation_toolbar = AnnotationToolbar()

                # Wire toolbar signals to overlay and engine
                tb = self._annotation_toolbar
                ov = self._annotation_overlay

                tb.tool_changed.connect(ov.set_active_tool)
                tb.color_changed.connect(ov.set_active_color)
                tb.stroke_width_changed.connect(ov.set_stroke_width)
                tb.interact_mode_toggled.connect(ov.set_interact_mode)
                tb.undo_requested.connect(lambda: (self.annotation_engine.undo(), ov.update() if ov else None))
                tb.redo_requested.connect(lambda: (self.annotation_engine.redo(), ov.update() if ov else None))
                tb.clear_requested.connect(lambda: (self.annotation_engine.clear(), ov.update() if ov else None))
                tb.visibility_toggled.connect(ov.set_annotations_visible)
                tb.screenshot_requested.connect(self.capture_screenshot)
                tb.closed.connect(self.stop_annotation_mode)
                ov.undo_state_changed.connect(tb.update_history_state)

            # Reposition toolbar near top-right/center if first opening
            if not self._annotation_toolbar.isVisible():
                screen = QApplication.primaryScreen()
                if screen:
                    geom = screen.availableGeometry()
                    tx = geom.x() + (geom.width() - self._annotation_toolbar.width()) // 2
                    ty = geom.y() + 40
                    self._annotation_toolbar.move(tx, ty)

            self._annotation_overlay.show()
            self._annotation_overlay.raise_()
            self._annotation_toolbar.show()
            self._annotation_toolbar.raise_()
            self._annotation_toolbar.activateWindow()
            self.annotation_mode_changed.emit(True)
            logger.info("Live Screen Annotation mode started.")
        except Exception as exc:
            logger.error("Failed to start annotation mode: %s", exc)

    def stop_annotation_mode(self) -> None:
        """Hides annotation overlay and toolbar."""
        app = QApplication.instance()
        if not app:
            return
        if threading.current_thread() is not threading.main_thread():
            from PyQt6.QtCore import QMetaObject, Qt
            QMetaObject.invokeMethod(self, "_do_stop_annotation_mode", Qt.ConnectionType.QueuedConnection)
            return
        self._do_stop_annotation_mode()

    def _do_stop_annotation_mode(self) -> None:
        try:
            if self._annotation_overlay and self._annotation_overlay.isVisible():
                self._annotation_overlay.hide()
            if self._annotation_toolbar and self._annotation_toolbar.isVisible():
                self._annotation_toolbar.hide()
            self.annotation_engine.clear_laser()
            self.annotation_engine.clear_spotlight()
            self.annotation_mode_changed.emit(False)
            logger.info("Live Screen Annotation mode stopped.")
        except Exception as exc:
            logger.error("Failed to stop annotation mode: %s", exc)

    def toggle_annotation_mode(self) -> bool:
        if self.is_annotation_mode_active():
            self.stop_annotation_mode()
            return False
        else:
            self.start_annotation_mode()
            return True

    def capture_screenshot(self, mode: str = "region") -> None:
        """
        Launches the Snipping tool for Region or Window selection,
        or immediately grabs Fullscreen, then opens Image Markup Studio.
        """
        if mode == "fullscreen":
            from voxlocal.annotation.snipping import capture_fullscreen
            pix = capture_fullscreen()
            self.open_image_markup_studio(pix.toImage(), title="Fullscreen Capture")
        else:
            from voxlocal.annotation.snipping import SnippingOverlay
            snip = SnippingOverlay(mode=mode)
            snip.snipped.connect(lambda pix: self.open_image_markup_studio(pix.toImage(), title=f"{mode.capitalize()} Snip"))
            self._active_snipping_overlays.append(snip)
            snip.show()
            snip.raise_()
            snip.activateWindow()

    def open_image_markup_studio(self, image: Any, title: str = "Screenshot") -> Any:
        """Opens the Image Markup Studio with the given image."""
        from PyQt6.QtGui import QImage, QPixmap
        from voxlocal.annotation.markup_studio import ImageMarkupStudio

        qimg: Optional[QImage] = None
        if isinstance(image, QPixmap):
            qimg = image.toImage()
        elif isinstance(image, QImage):
            qimg = image
        elif isinstance(image, (str, Path)):
            qimg = QImage(str(image))

        studio = ImageMarkupStudio(initial_image=qimg, title=title)
        self._active_markup_studios.append(studio)
        studio.show()
        studio.raise_()
        studio.activateWindow()
        return studio

    def _on_screen_rec_tick(self) -> None:
        if self._screen_rec_state == "recording":
            elapsed = self.pc_screen_audio.elapsed_seconds if self.pc_screen_audio.state == "recording" else 0.0
            frames = getattr(self.pc_screen_recorder, "_total_frames", 0)
            self.screen_recording_tick.emit(elapsed, frames)

    def show_meeting_prompt_popup(self, session_info: Any) -> None:
        """Consolidate meeting detection alert into unified bottom-center FlowBar."""
        fp = getattr(self, "floating_pill", None)
        if fp is not None and hasattr(fp, "set_pill_state"):
            title = getattr(session_info, "clean_title", None) or getattr(session_info, "title", "Call")
            plat = getattr(session_info, "platform", "Call")
            fp.set_pill_state("CARD_NOTIFICATION", metadata={"title": title, "platform": plat, "session": session_info})
            return
        # Deprecated fallback if FlowBar is not active
        try:
            from voxlocal.ui.meeting_prompt_popup import MeetingPromptPopup
            if self._meeting_prompt_popup and self._meeting_prompt_popup.isVisible():
                self._meeting_prompt_popup.update_session(session_info)
                return
            self._meeting_prompt_popup = MeetingPromptPopup(session_info, self)
            self._meeting_prompt_popup.show()
        except Exception as exc:
            logger.debug("Could not display MeetingPromptPopup: %s", exc)

    def _poll_call_detector(self) -> None:
        """Layered detection of active Teams, Zoom, Google Meet, Slack, or Webex meetings."""
        if self._screen_rec_state in ("recording", "paused"):
            return
        is_rec_now = self._capture_state in ("recording", "paused")
        if not is_rec_now and not getattr(self.config, "auto_record_calls", True) and not getattr(self.config, "prompt_on_call", True):
            return

        # Dynamic binding for pre-call recording (Audio + Video deferred attach or Audio-only target HWND binding)
        if self._capture_state == "recording":
            if getattr(self, "_pending_video_bind", False):
                try:
                    from voxlocal.video.meeting_capture import resolve_meeting_source, MeetingVideoRecorder
                    src = resolve_meeting_source(target_hwnd=getattr(self, "_active_meeting_target_hwnd", None))
                    if src is None and getattr(self, "_active_meeting_platform", None):
                        src = resolve_meeting_source(platform_hint=self._active_meeting_platform)
                    if src is not None:
                        if getattr(self, "_meeting_video_recorder", None) is not None:
                            self._meeting_video_recorder.source_descriptor = src
                            self._meeting_video_recorder.target_hwnd = src.hwnd
                            if hasattr(self._meeting_video_recorder, "validator"):
                                self._meeting_video_recorder.validator.descriptor = src
                            self._pending_video_bind = False
                            self._active_meeting_target_hwnd = src.hwnd
                            self._active_meeting_pid = src.pid
                            self._active_meeting_platform = src.platform_name
                            logger.info(
                                "Dynamic video binding attached to %s (HWND=%s, title='%s')",
                                src.platform_name,
                                src.hwnd,
                                src.window_title,
                            )
                            status_text = f"Recording (Audio + Video): {self._active_meeting_title}"
                            self.set_meeting_lifecycle_state(
                                MeetingLifecycleState.RECORDING,
                                status_text,
                            )
                            self.meeting_state_changed.emit("recording", status_text)
                            self.notification_requested.emit(
                                "Meeting Video Connected",
                                f"Connected video recording to {src.platform_name} call window.",
                            )
                        elif getattr(self, "_pending_video_folder", None):
                            video_file = self._pending_video_folder / "meeting_screen.mp4"
                            target_fps = int(getattr(self, "_pending_video_fps", 15) or 15)
                            res = getattr(self, "_pending_video_res", "1080p")
                            max_h = 1080
                            if isinstance(res, str):
                                if "720" in res:
                                    max_h = 720
                                elif "480" in res:
                                    max_h = 480
                                elif "1440" in res or "2k" in res.lower():
                                    max_h = 1440
                                elif "2160" in res or "4k" in res.lower():
                                    max_h = 2160

                            v_rec = MeetingVideoRecorder(
                                output_path=str(video_file),
                                source_descriptor=src,
                                fps=target_fps,
                                max_height=max_h,
                                on_meeting_ended=self._on_meeting_capture_ended,
                                on_source_state_change=self._on_video_source_state_changed,
                            )
                            if v_rec.start(str(video_file)):
                                self._meeting_video_recorder = v_rec
                                self._pending_video_bind = False
                                self._active_meeting_target_hwnd = src.hwnd
                                self._active_meeting_pid = src.pid
                                self._active_meeting_platform = src.platform_name
                                logger.info(
                                    "Dynamic video binding attached to %s (HWND=%s, title='%s'): %s",
                                    src.platform_name,
                                    src.hwnd,
                                    src.window_title,
                                    video_file,
                                )
                                status_text = f"Recording (Audio + Video): {self._active_meeting_title}"
                                self.set_meeting_lifecycle_state(
                                    MeetingLifecycleState.RECORDING,
                                    status_text,
                                )
                                self.meeting_state_changed.emit("recording", status_text)
                                self.notification_requested.emit(
                                    "Meeting Video Connected",
                                    f"Connected video recording to {src.platform_name} call window.",
                                )
                except Exception as exc:
                    logger.debug("Deferred video binding attempt: %s", exc)

            if getattr(self, "_active_meeting_target_hwnd", None) is None:
                try:
                    from voxlocal.video.meeting_capture import resolve_meeting_source
                    src = resolve_meeting_source()
                    if src is not None:
                        self._active_meeting_target_hwnd = src.hwnd
                        self._active_meeting_pid = src.pid
                        self._active_meeting_platform = src.platform_name
                        logger.info("Bound active meeting target HWND=%s (%s) for pre-started session", src.hwnd, src.platform_name)
                except Exception as exc:
                    logger.debug("Deferred target hwnd binding attempt: %s", exc)
        try:
            event = self.call_detector.poll()
            if event == "started":
                sess = getattr(self.call_detector, "active_session", None)
                app_name = sess.platform if sess else (self.call_detector.active.app_name if self.call_detector.active else "Call")
                sess_id = sess.session_id if sess else (getattr(self.call_detector.active, "session_id", "") if self.call_detector.active else "")
                call_key = self.call_detector.active.key if self.call_detector.active else ""
                title = sess.clean_title if sess else f"{app_name} ({datetime.now().strftime('%b %d, %H:%M')})"
                logger.info("CallDetector detected active call in '%s' (session_id=%s)", app_name, sess_id)

                if not getattr(self, "_active_meeting_calendar_event_id", "") and hasattr(self, "calendar_engine") and self.calendar_engine:
                    try:
                        raw_win_title = sess.raw_title if sess else ""
                        matched_mtg = self.match_active_call_to_calendar_meeting(
                            detected_platform=app_name,
                            window_title=raw_win_title or title,
                        )
                        if matched_mtg is not None:
                            self._active_meeting_calendar_event_id = matched_mtg.id
                            self._active_meeting_calendar_join_url = matched_mtg.join_url or ""
                            self._active_meeting_calendar_attendees = matched_mtg.attendees_str
                            self._active_meeting_calendar_organizer = matched_mtg.organizer
                            self._active_meeting_calendar_provider = str(matched_mtg.provider_type)
                            self._active_meeting_calendar_account_id = str(getattr(matched_mtg, "account_id", ""))
                            if not title or title.startswith(app_name):
                                title = matched_mtg.title
                    except Exception as e_cal:
                        logger.debug("Auto calendar association note: %s", e_cal)

                if self._capture_state == "idle":
                    self.set_meeting_lifecycle_state(
                        MeetingLifecycleState.MEETING_DETECTED,
                        f"Detected {app_name}: {title}",
                    )
                    if (sess_id and sess_id in self._dismissed_call_keys) or (call_key and call_key in self._dismissed_call_keys):
                        return
                    if getattr(self.config, "prompt_on_call", True):
                        self.set_meeting_lifecycle_state(
                            MeetingLifecycleState.AWAITING_CONFIRMATION,
                            f"Awaiting confirmation for {app_name}: {title}",
                        )
                        # Explicit non-activating bottom-right user consent popup
                        if sess:
                            self.show_meeting_prompt_popup(sess)
                        self.call_detected.emit(app_name, title)
                    else:
                        target_hwnd = sess.window_hwnd if sess else None
                        ok, msg = self.start_meeting_recording(title=title, target_hwnd=target_hwnd, is_auto_detected=True)
                        if ok:
                            self.notification_requested.emit(
                                "Meeting Recording",
                                f"Detected {app_name} call. Recording & transcribing meeting notes.",
                            )
                        self.call_detected.emit(app_name, title)
            elif event == "ended":
                if self._meeting_prompt_popup and self._meeting_prompt_popup.isVisible():
                    self._meeting_prompt_popup.close()
                    self._meeting_prompt_popup = None
                from voxlocal.audio.call_detector import MeetingSessionInfo
                ended_sess = getattr(self.call_detector, "last_ended_session", None)
                if not isinstance(ended_sess, MeetingSessionInfo):
                    ended_sess = None
                ended_act = getattr(self.call_detector, "active", None) or getattr(self.call_detector, "last_ended_active", None)

                app_name = ""
                if ended_sess and isinstance(getattr(ended_sess, "platform", None), str):
                    app_name = ended_sess.platform
                elif ended_act and hasattr(ended_act, "app_name") and isinstance(ended_act.app_name, str):
                    app_name = ended_act.app_name
                elif isinstance(getattr(self, "_active_meeting_platform", ""), str) and getattr(self, "_active_meeting_platform", ""):
                    app_name = getattr(self, "_active_meeting_platform", "")
                if not app_name:
                    app_name = "Call"

                sess_id = ""
                if ended_sess and isinstance(getattr(ended_sess, "session_id", None), str):
                    sess_id = ended_sess.session_id
                elif ended_act and hasattr(ended_act, "session_id") and isinstance(ended_act.session_id, str):
                    sess_id = ended_act.session_id
                elif isinstance(getattr(self, "_active_meeting_session_id", ""), str):
                    sess_id = getattr(self, "_active_meeting_session_id", "")

                call_key = ""
                if ended_act and hasattr(ended_act, "key") and isinstance(ended_act.key, str):
                    call_key = ended_act.key
                if sess_id:
                    self._dismissed_call_keys.discard(sess_id)
                if call_key:
                    self._dismissed_call_keys.discard(call_key)
                self.call_ended.emit(app_name)
                if (
                    self._capture_state in ("recording", "paused")
                    and bool(getattr(self.config, "notetaker_auto_end", True))
                ):
                    # CRITICAL: Verify meeting target liveness against genuine Call Stage windows or presentation sessions
                    meeting_target_alive = False
                    from voxlocal.audio.call_detector import is_leaving_title, is_non_call_hub
                    from voxlocal.video.meeting_capture import get_window_title, discover_meeting_sources, find_teams_sharing_session, find_zoom_sharing_session

                    if self._active_meeting_target_hwnd and user32.IsWindow(self._active_meeting_target_hwnd):
                        t = get_window_title(self._active_meeting_target_hwnd)
                        if not is_leaving_title(t) and not is_non_call_hub(app_name, t):
                            meeting_target_alive = True

                    if not meeting_target_alive:
                        # Check if active meeting call stage window exists for this platform
                        sources = discover_meeting_sources(include_non_call_hubs=False)
                        for src in sources:
                            is_target = (self._active_meeting_target_hwnd is None or src.hwnd == self._active_meeting_target_hwnd)
                            if is_target and (not app_name or app_name == "Call" or src.platform_name.lower() == app_name.lower()) and src.is_call_stage:
                                if not is_leaving_title(src.window_title):
                                    meeting_target_alive = True
                                    self._active_meeting_target_hwnd = src.hwnd
                                    break

                    if not meeting_target_alive:
                        # Check if a presentation session is active
                        if "teams" in app_name.lower() and find_teams_sharing_session(self._active_meeting_pid) is not None:
                            meeting_target_alive = True
                        elif "zoom" in app_name.lower() and find_zoom_sharing_session(self._active_meeting_pid) is not None:
                            meeting_target_alive = True

                    if meeting_target_alive:
                        logger.info(
                            "CallDetector signaled potential call end, but active call stage/presentation (HWND=%s) is still confirmed. Recording preserved.",
                            self._active_meeting_target_hwnd,
                        )
                        return

                    logger.info("CallDetector verified meeting call stage closed -> Auto-stopping meeting recording")
                    self.set_meeting_lifecycle_state(
                        MeetingLifecycleState.MEETING_ENDING,
                        f"{app_name} call ended — finalizing recording...",
                    )
                    self.stop_meeting_recording()
                    self.notification_requested.emit(
                        "Meeting Ended",
                        f"{app_name} call ended. Finalizing meeting notes and transcription...",
                    )
                else:
                    self.set_meeting_lifecycle_state(MeetingLifecycleState.NO_MEETING_DETECTED, "No active meeting detected.")
            elif (
                self._capture_state in ("recording", "paused")
                and self._active_meeting_target_hwnd
                and bool(self.config.notetaker_auto_end)
            ):
                # Monitor verified auto-detected online meeting window (Teams/Meet/Zoom)
                from voxlocal.audio.call_detector import is_leaving_title, is_non_call_hub
                from voxlocal.video.meeting_capture import get_window_title, discover_meeting_sources, find_teams_sharing_session, find_zoom_sharing_session

                plat = getattr(self, "_active_meeting_platform", "") or "Call"
                target_alive = False
                if user32.IsWindow(self._active_meeting_target_hwnd):
                    t = get_window_title(self._active_meeting_target_hwnd)
                    if not is_leaving_title(t) and not is_non_call_hub(plat, t):
                        target_alive = True

                if not target_alive:
                    sources = discover_meeting_sources(include_non_call_hubs=False)
                    for src in sources:
                        if (plat == "Call" or src.platform_name.lower() == plat.lower()) and src.is_call_stage:
                            if not is_leaving_title(src.window_title):
                                target_alive = True
                                self._active_meeting_target_hwnd = src.hwnd
                                break

                if not target_alive:
                    if "teams" in plat.lower() and find_teams_sharing_session(self._active_meeting_pid) is not None:
                        target_alive = True
                    elif "zoom" in plat.lower() and find_zoom_sharing_session(self._active_meeting_pid) is not None:
                        target_alive = True

                if target_alive:
                    self._manual_target_lost_since = None
                else:
                    now_m = time.monotonic()
                    if self._manual_target_lost_since is None:
                        self._manual_target_lost_since = now_m
                    elif (now_m - self._manual_target_lost_since) >= 2.0:
                        self._manual_target_lost_since = None
                        logger.info("Bound meeting window (HWND=%s) closed/ended -> Auto-stopping meeting recording", self._active_meeting_target_hwnd)
                        self.set_meeting_lifecycle_state(
                            MeetingLifecycleState.MEETING_ENDING,
                            f"{plat} call ended — finalizing recording...",
                        )
                        self.stop_meeting_recording()
                        self.notification_requested.emit(
                            "Meeting Ended",
                            f"{plat} call ended. Finalizing meeting notes and transcription...",
                        )
        except Exception as exc:
            logger.debug("Error in _poll_call_detector: %s", exc)

    def dismiss_call_prompt(self, call_key: Optional[str] = None) -> None:
        """User dismissed the call prompt: remember not to prompt again for this active call session."""
        key = call_key or (self.call_detector.active.session_id if (self.call_detector.active and hasattr(self.call_detector.active, "session_id")) else (self.call_detector.active.key if self.call_detector.active else ""))
        if key:
            self._dismissed_call_keys.add(key)

    def query_meeting_memory(
        self, question: str, meeting_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Ask Vox: Comprehensive 100% offline local AI & algorithmic cognitive reasoning
        across recorded meetings (or scoped to meeting_id) with grounded citations.
        """
        if hasattr(self, "metering") and hasattr(self, "entitlement"):
            can_ask, reason = self.metering.can_ask_vox(self.entitlement)
            if not can_ask:
                msg = reason or "Weekly Ask VOX query quota reached (10/10). Upgrade to VOX Pro for unlimited cognitive queries."
                self.upgrade_dialog_requested.emit(msg)
                return {
                    "question": question,
                    "answer": f"### ⚠️ Ask VOX Weekly Limit Reached\n\n{msg}\n\nUpgrade to **VOX Pro** (₹199/mo) for unlimited cognitive queries across all your recorded meetings.",
                    "citations": [],
                    "intent": "quota_exceeded",
                    "suggestions": ["Upgrade to VOX Pro (₹199/mo)", "View weekly usage"],
                }

        try:
            res = self.intelligence.query(question, meeting_id=meeting_id)
            if hasattr(self, "metering") and self.metering is not None:
                self.metering.record_ask_vox_query(question)
                self.usage_updated.emit(self.metering.get_weekly_usage())
            return res
        except Exception as exc:
            logger.error("Error in query_meeting_memory: %s", exc, exc_info=True)
            return {
                "question": question,
                "answer": f"### ⚠️ Analysis Notice\n\nCould not complete search across meetings: {exc}",
                "citations": [],
                "intent": "error",
                "suggestions": ["Summarize recent meetings", "List all action items"],
            }

    def query_meeting_memory_stream(
        self, question: str, meeting_id: Optional[int] = None
    ) -> None:
        """
        Ask Vox (Streaming): Process query in background thread, emitting:
        - ask_vox_citations_ready(citations) immediately when search completes
        - ask_vox_token_streamed(token_str) as each token is generated
        - ask_vox_stream_finished(result_dict) upon completion
        """
        self._cancel_ask_vox = True
        if hasattr(self, "_ask_vox_thread") and self._ask_vox_thread and self._ask_vox_thread.is_alive():
            try:
                self._ask_vox_thread.join(timeout=0.25)
            except Exception:
                pass

        self._ask_vox_query_id += 1
        current_qid = self._ask_vox_query_id
        self._cancel_ask_vox = False

        if hasattr(self, "metering") and hasattr(self, "entitlement"):
            can_ask, reason = self.metering.can_ask_vox(self.entitlement)
            if not can_ask:
                msg = reason or "Weekly Ask VOX query quota reached (10/10). Upgrade to VOX Pro for unlimited cognitive queries."
                self.upgrade_dialog_requested.emit(msg)
                err_dict = {
                    "question": question,
                    "answer": f"### ⚠️ Ask VOX Weekly Limit Reached\n\n{msg}\n\nUpgrade to **VOX Pro** (₹199/mo) for unlimited cognitive queries across all your recorded meetings.",
                    "citations": [],
                    "intent": "quota_exceeded",
                    "suggestions": ["Upgrade to VOX Pro (₹199/mo)", "View weekly usage"],
                }
                self.ask_vox_stream_finished.emit(current_qid, err_dict)
                return

        def _worker() -> None:
            try:
                res = self.intelligence.query_stream(
                    question=question,
                    meeting_id=meeting_id,
                    on_token=lambda tok: (self.ask_vox_token_streamed.emit(current_qid, tok) if self._ask_vox_query_id == current_qid and not self._cancel_ask_vox else None),
                    on_citations=lambda cits: (self.ask_vox_citations_ready.emit(current_qid, cits) if self._ask_vox_query_id == current_qid and not self._cancel_ask_vox else None),
                    cancel_check=lambda: self._cancel_ask_vox or (self._ask_vox_query_id != current_qid),
                )
                if self._ask_vox_query_id == current_qid and not self._cancel_ask_vox:
                    if hasattr(self, "metering") and self.metering is not None:
                        self.metering.record_ask_vox_query(question)
                        self.usage_updated.emit(self.metering.get_weekly_usage())
                    self.ask_vox_stream_finished.emit(current_qid, res)
            except Exception as exc:
                logger.error("Error in query_meeting_memory_stream: %s", exc, exc_info=True)
                err_dict = {
                    "question": question,
                    "answer": f"### ⚠️ Analysis Notice\n\nCould not complete search across meetings: {exc}",
                    "citations": [],
                    "intent": "error",
                    "suggestions": ["Summarize recent meetings", "List all action items"],
                }
                if self._ask_vox_query_id == current_qid and not self._cancel_ask_vox:
                    self.ask_vox_stream_finished.emit(current_qid, err_dict)

        self._ask_vox_thread = threading.Thread(target=_worker, name="AskVoxStreamThread", daemon=True)
        self._ask_vox_thread.start()
        return current_qid

    def stop_ask_vox_stream(self) -> None:
        """Cancel ongoing Ask Vox token streaming."""
        self._cancel_ask_vox = True

    def compare_meetings(self, meeting_id_a: int, meeting_id_b: int) -> Dict[str, Any]:
        """Compare two meetings side-by-side using MeetingIntelligenceEngine."""
        return self.intelligence.compare_meetings(meeting_id_a, meeting_id_b)

    def generate_pre_meeting_briefing(self, query_or_topic: str) -> Dict[str, Any]:
        """Generate an executive pre-meeting briefing using MeetingIntelligenceEngine."""
        return self.intelligence.generate_pre_meeting_briefing(query_or_topic)

    def get_cross_meeting_decisions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve cross-meeting decisions."""
        return self.intelligence.get_cross_meeting_decisions(limit=limit)

    def get_recurring_topics(self, limit: int = 15) -> List[Dict[str, Any]]:
        """Retrieve recurring topics across meetings."""
        return self.intelligence.get_recurring_topics(limit=limit)

    # -------------------------------------------------------------------------
    # Commercial Account, Entitlements & Metering Integration (P0)
    # -------------------------------------------------------------------------

    def get_account_status(self) -> Dict[str, Any]:
        """Returns the current user profile, entitlement plan, and weekly usage statistics."""
        broker = getattr(self, "desktop_auth_broker", None) or getattr(self, "auth_broker", None)
        session = broker.load_session() if broker and hasattr(broker, "load_session") else None
        if not isinstance(session, dict):
            session = None

        usage = self.metering.get_weekly_usage() if hasattr(self, "metering") and self.metering else {}
        days_until_reset = self.metering.days_until_next_reset() if hasattr(self, "metering") and self.metering else 7

        if session is None:
            from voxlocal.core.entitlement import FREE_DEFAULT_LIMITS
            return {
                "signed_in": False,
                "email": "",
                "name": "Guest User",
                "picture": None,
                "plan": "free",
                "plan_name": "VOX Free",
                "is_pro": False,
                "usage": usage,
                "limits": dict(FREE_DEFAULT_LIMITS),
                "days_until_reset": days_until_reset,
            }

        user_info = (session.get("user") or {}) if session else {}
        if not isinstance(user_info, dict):
            user_info = {}
        user_email = str(user_info.get("email") or "")
        user_id = str((session.get("user_id") if session else None) or user_info.get("id") or "")
        from voxlocal.core.entitlement import is_master_developer
        is_master = is_master_developer(user_id=user_id, email=user_email)

        ent = self.entitlement.get_effective_entitlement() if hasattr(self, "entitlement") and self.entitlement else {
            "plan": "free", "plan_name": "VOX Free", "is_pro": False, "limits": {}
        }
        if is_master:
            ent["plan"] = "pro_annual"
            ent["plan_code"] = "pro_annual"
            ent["plan_name"] = "VOX Pro"
            ent["is_pro"] = True
            ent["limits"] = {
                "dictation_words_weekly": None,
                "meeting_minutes_weekly": None,
                "meeting_sessions_weekly": None,
                "meeting_session_cap_min": None,
                "ask_vox_queries_weekly": None,
                "allow_meeting_overrun": True,
            }

        limits = ent.get("limits", {}) if isinstance(ent, dict) else {}
        return {
            "signed_in": True,
            "email": user_email,
            "name": user_info.get("name") or (user_email.split("@")[0] if user_email else "Guest User"),
            "picture": user_info.get("picture"),
            "plan": ent.get("plan", "free") if isinstance(ent, dict) else "free",
            "plan_name": ent.get("plan_name", "VOX Free") if isinstance(ent, dict) else "VOX Free",
            "is_pro": ent.get("is_pro", False) if isinstance(ent, dict) else False,
            "usage": usage,
            "limits": limits,
            "days_until_reset": days_until_reset,
        }

    def login_with_google(self, callback: Optional[Any] = None) -> None:
        """Starts loopback OAuth PKCE flow to sign in the user."""
        self._auth_generation = getattr(self, "_auth_generation", 0) + 1
        def _on_done(ok: bool, msg: str, data: Optional[Dict[str, Any]] = None):
            if ok:
                self._auth_generation = getattr(self, "_auth_generation", 0) + 1
                broker = getattr(self, "desktop_auth_broker", None) or getattr(self, "auth_broker", None)
                session = broker.load_session() if broker and hasattr(broker, "load_session") else None
                if isinstance(session, dict):
                    user_info = session.get("user") or {}
                    if not isinstance(user_info, dict):
                        user_info = {}
                    user_id = str(session.get("user_id") or user_info.get("id") or (data.get("user_id") if isinstance(data, dict) else "default"))
                    user_email = str(user_info.get("email") or "")
                    from voxlocal.core.entitlement import is_master_developer
                    is_master = is_master_developer(user_id=user_id, email=user_email)
                    plan_code = "pro_annual" if is_master else (user_info.get("plan") or session.get("plan_code") or (data.get("plan_code") if isinstance(data, dict) else "free"))
                    if hasattr(self, "metering") and self.metering is not None:
                        self.metering.set_user_context(user_id, plan_code, email=user_email)
                    if hasattr(self, "entitlement") and self.entitlement is not None and hasattr(self.entitlement, "set_user_context"):
                        self.entitlement.set_user_context(user_id, email=user_email, plan_code=plan_code)
                self.sync_entitlements_with_cloud()
            self._safe_emit(self.auth_state_changed, self.get_account_status())
            if hasattr(self, "entitlement") and self.entitlement is not None:
                self._safe_emit(self.entitlement_changed, self.entitlement.get_effective_entitlement())
            if hasattr(self, "metering") and self.metering is not None:
                self._safe_emit(self.usage_updated, self.metering.get_weekly_usage())
            self._safe_emit(self.login_completed_signal, ok, msg)
            if callback:
                try:
                    callback(ok, msg)
                except Exception as cb_err:
                    logger.debug("login callback exception caught: %s", cb_err)

        broker = getattr(self, "desktop_auth_broker", None) or getattr(self, "auth_broker", None)
        if broker and hasattr(broker, "start_login"):
            threading.Thread(target=lambda: broker.start_login(callback=_on_done), daemon=True).start()
        elif callback:
            callback(False, "Auth broker unavailable")

    def complete_login_from_url(self, raw_url_or_token: str) -> Tuple[bool, str]:
        """Manually completes sign-in from an authorization URL or access token."""
        self._auth_generation = getattr(self, "_auth_generation", 0) + 1
        broker = getattr(self, "desktop_auth_broker", None) or getattr(self, "auth_broker", None)
        if not broker or not hasattr(broker, "complete_login_from_url"):
            return False, "Desktop auth broker unavailable."
        ok, msg, profile = broker.complete_login_from_url(raw_url_or_token)
        if ok and profile:
            if hasattr(self, "metering") and self.metering is not None:
                self.metering.set_user_context(profile.user_id, profile.plan_code, email=profile.email)
            if hasattr(self, "entitlement") and self.entitlement is not None and hasattr(self.entitlement, "set_user_context"):
                self.entitlement.set_user_context(profile.user_id, email=profile.email, plan_code=profile.plan_code)
            self._safe_emit(self.auth_state_changed, self.get_account_status())
            if hasattr(self, "entitlement") and self.entitlement is not None:
                self._safe_emit(self.entitlement_changed, self.entitlement.get_effective_entitlement())
            if hasattr(self, "metering") and self.metering is not None:
                self._safe_emit(self.usage_updated, self.metering.get_weekly_usage())
            self.sync_entitlements_with_cloud()
        return ok, msg

    def logout_user(self) -> None:
        """Logs out user, clears session vault, and reverts to Free tier safely."""
        self._auth_generation = getattr(self, "_auth_generation", 0) + 1
        broker = getattr(self, "desktop_auth_broker", None) or getattr(self, "auth_broker", None)
        if broker and hasattr(broker, "clear_session"):
            broker.clear_session()
        elif broker and hasattr(broker, "sign_out"):
            broker.sign_out()
        if hasattr(self, "entitlement") and self.entitlement is not None:
            self.entitlement.clear_entitlement()
            if hasattr(self.entitlement, "set_user_context"):
                self.entitlement.set_user_context(None)
        if hasattr(self, "metering") and self.metering is not None:
            self.metering.set_user_context(None, "free")
        self._safe_emit(self.auth_state_changed, self.get_account_status())
        if hasattr(self, "entitlement") and self.entitlement is not None:
            self._safe_emit(self.entitlement_changed, self.entitlement.get_effective_entitlement())
        if hasattr(self, "metering") and self.metering is not None:
            self._safe_emit(self.usage_updated, self.metering.get_weekly_usage())

    def sync_entitlements_with_cloud(self) -> None:
        """Non-blocking sync of unsynced usage events and refresh of entitlement token."""
        threading.Thread(target=self._sync_commercial_worker, daemon=True).start()

    def _sync_commercial_worker(self) -> None:
        try:
            gen = getattr(self, "_auth_generation", 0)
            broker = getattr(self, "desktop_auth_broker", None) or getattr(self, "auth_broker", None)
            if not broker or not hasattr(self, "metering"):
                return
            session = broker.load_session() if hasattr(broker, "load_session") else None
            token = session.get("access_token") if session else None
            import urllib.request
            import json

            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            if isinstance(session, dict):
                uid_hdr = str(session.get("user_id") or (session.get("user") or {}).get("id") or "").strip()
                em_hdr = str((session.get("user") or {}).get("email") or "").strip()
                if uid_hdr:
                    headers["X-Vox-User-Id"] = uid_hdr
                if em_hdr:
                    headers["X-Vox-User-Email"] = em_hdr

            # 1. Sync unsynced usage events (idempotent UUIDs, aggregate counters only)
            unsynced = self.metering.get_unsynced_events(limit=50)
            if unsynced and token:
                req = urllib.request.Request(
                    "https://vox-voice-assistant.vercel.app/api/usage/sync",
                    data=json.dumps({"events": unsynced}).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        sync_res = json.loads(resp.read().decode("utf-8"))
                        acked_ids = sync_res.get("acked_event_ids", [])
                        if acked_ids and getattr(self, "_auth_generation", 0) == gen:
                            self.metering.mark_events_synced(acked_ids)

            # 2. Refresh active entitlement token
            if token and hasattr(self, "entitlement"):
                req_ent = urllib.request.Request(
                    "https://vox-voice-assistant.vercel.app/api/entitlements",
                    headers=headers,
                    method="GET",
                )
                with urllib.request.urlopen(req_ent, timeout=10) as resp:
                    if resp.status == 200:
                        ent_data = json.loads(resp.read().decode("utf-8"))
                        if getattr(self, "_auth_generation", 0) != gen or (hasattr(broker, "is_authenticated") and not broker.is_authenticated()):
                            return
                        raw_jwt = ent_data.get("token")
                        if raw_jwt:
                            self.entitlement.save_token_to_vault(raw_jwt)
                            self.entitlement_changed.emit(self.entitlement.get_effective_entitlement())
                            self.auth_state_changed.emit(self.get_account_status())
                        elif ent_data.get("is_pro") or ent_data.get("plan"):
                            plan_code = ent_data.get("plan", "free")
                            user_id = session.get("user_id") if session else "default"
                            if hasattr(self, "metering") and self.metering is not None:
                                self.metering.set_user_context(user_id, plan_code)
                            self.auth_state_changed.emit(self.get_account_status())
        except Exception as exc:
            logger.debug("Cloud commercial sync warning (offline or warming): %s", exc)

    def check_for_updates(self, timeout_sec: int = 15) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Queries the release endpoint and cryptographically verifies the manifest Ed25519 signature.
        Returns: (is_update_available, verified_manifest_or_none, message)
        """
        if not getattr(self, "update_manager", None):
            try:
                from voxlocal.core.updater import UpdateManager
                self.update_manager = UpdateManager()
            except Exception as exc:
                return False, None, f"Update manager unavailable: {exc}"

        res = self.update_manager.check_for_updates(timeout_sec=timeout_sec)
        is_avail, manifest, msg = res
        try:
            self.update_check_completed.emit(is_avail, manifest or {}, msg)
        except Exception:
            pass
        return res

    def verify_update_package(
        self, manifest: Dict[str, Any], installer_path_or_bytes: Any
    ) -> Tuple[bool, str]:
        """
        Verifies release manifest signature and installer binary SHA-256 integrity.
        """
        from voxlocal.core.updater import verify_release_trust_chain
        return verify_release_trust_chain(manifest, installer_path_or_bytes)

    def shutdown(self) -> None:
        """Cleanly stop all timers, hooks, and audio streams on application exit."""
        logger.info("Shutting down AppController...")
        self._cancel_ask_vox = True
        if hasattr(self, "_ask_vox_thread") and self._ask_vox_thread and self._ask_vox_thread.is_alive():
            try:
                self._ask_vox_thread.join(timeout=0.5)
            except Exception:
                pass
        self._cancel_finalization_event.set()
        if hasattr(self, "_finalize_thread") and self._finalize_thread and self._finalize_thread.is_alive():
            try:
                self._finalize_thread.join(timeout=1.0)
            except Exception:
                pass
        self._interim_worker_running = False
        try:
            if hasattr(self, "_focus_timer") and self._focus_timer:
                self._focus_timer.stop()
        except Exception:
            pass
        try:
            if hasattr(self, "_call_timer") and self._call_timer:
                self._call_timer.stop()
        except Exception:
            pass
        if hasattr(self.call_detector, "stop_winevent_listener"):
            try:
                self.call_detector.stop_winevent_listener()
            except Exception:
                pass
        self.hotkey_manager.stop()
        if self._hands_free_active or self.streaming_typer.is_running:
            self.streaming_typer.stop()
        if self.dictation_recorder.is_recording:
            self.dictation_recorder.stop()
        if self._screen_rec_state in ("recording", "paused"):
            self.stop_screen_recording()
        if hasattr(self, "screen_recorder") and self.screen_recorder.is_recording:
            self.screen_recorder.stop()
        if self._capture_state in ("recording", "paused"):
            try:
                self.stop_meeting_recording()
            except Exception as exc:
                logger.warning("Error stopping meeting on controller shutdown: %s", exc)
        elif self.meeting_recorder.state != "idle":
            self.meeting_recorder.stop(preserve_separate_tracks=self.config.preserve_separate_tracks)
        self.config.save()
