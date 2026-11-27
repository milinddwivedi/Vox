"""
Application configuration. Persisted as JSON in data/config.json.
Only settings a user actually needs are exposed.
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

from voxlocal.core.logger import get_app_root, get_logger, get_user_data_root, get_user_models_root

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

logger = get_logger("config")


def get_local_timezone_name() -> str:
    """Resolve the current OS local timezone display name cleanly."""
    try:
        tz = datetime.now().astimezone().tzname()
        if tz and tz.strip():
            return tz.strip()
    except Exception:
        pass
    return "Local"


@dataclass
class AppConfig:
    # Storage
    storage_dir: str = ""
    models_dir: str = ""
    retention_days: int = 0          # 0 = keep forever

    # Speech engine
    whisper_model: str = "large-v3-turbo"  # large-v3-turbo (Best Accuracy & Speed) | large-v3 | small | base
    compute_device: str = "auto"     # auto | cpu | cuda
    cpu_threads: int = 8
    language: str = "en+hi"          # en+hi (Auto Switch) | hi | hi_latn | en | auto
    transcribe_profile: str = "balanced"  # fast | balanced | max_accuracy
    enable_hardware_adaptive_asr: bool = True
    enable_hf_asr_rc: bool = False
    hf_asr_model_id: str = "Qwen/Qwen3-ASR-0.6B-hf"
    live_asr_backend: str = "auto"   # auto | faster_whisper | hf_qwen3_asr
    offline_asr_backend: str = "auto"  # auto | faster_whisper | hf_qwen3_asr
    verbatim_transcript_protection: bool = True
    enable_selective_verification: bool = True

    # Local Post-Call English Meeting ASR + Diarization Pipeline (v2.0 Single-Winner)
    meeting_asr_model: str = "cohere-transcribe-en-int8"
    meeting_diarization_enabled: bool = True
    meeting_diarization_threshold: float = 0.72
    meeting_echo_bleed_suppression: bool = True
    meeting_glossary_correction: bool = True

    # Phase 2 Adaptive Pipeline & Live Meeting Flags (retired for meetings; retained on schema for backward-compatible config loading)
    enable_phase2_asr_pipeline: bool = False
    asr_session_mode: str = "english"
    live_english_backend: str = "disabled"
    live_hinglish_backend: str = "disabled"
    offline_english_backend: str = "cohere_transcribe"
    offline_hinglish_backend: str = "disabled"
    selective_second_pass_enabled: bool = False
    selective_verifier_backend: str = "disabled"
    selective_logprob_threshold: float = -0.45
    selective_min_wps: float = 0.75
    selective_max_utterances_per_meeting: int = 40
    selective_enable_hinglish_cue_routing: bool = False
    live_preview_enabled: bool = False
    live_preview_max_backlog_sec: float = 8.0
    post_call_allow_cpu_verifier: bool = False
    post_call_adaptive_english_parakeet: bool = False
    post_call_punctuation_polish: bool = True
    # Live GPU VRAM headroom thresholds (MB) before loading optional CUDA models
    gpu_min_free_vram_hf_mb: int = 2000
    gpu_min_free_vram_ct2_mb: int = 1100

    # Local LLM Engine (Offline Meeting Intelligence, Knowledge & Ask VOX)
    llm_model: str = "Qwen3.5-4B-Q4_K_M.gguf"
    llm_enabled: bool = True
    llm_device: str = "auto"         # auto | gpu | cpu
    llm_idle_unload_sec: int = 300

    # Live voice typing (hands-free)
    typing_hotkey: str = "ctrl+shift+space"
    enable_voice_typing_hotkey: bool = True
    pause_to_type_sec: float = 0.65   # silence that ends a phrase and types it (fast Wispr-like endpointing)
    voice_commands: bool = True      # "send it", "delete that", "stop listening", ...
    spoken_punctuation: bool = True
    mic_device_index: int | None = None
    mic_device_name: str = ""
    mic_gain: float = 1.0            # Digital microphone volume scale (1.0 = pristine unity gain, DSP AGC handles broadcast loudness)

    # Meetings
    meeting_hotkey: str = "ctrl+alt+r"
    auto_record_calls: bool = True   # Read-AI style: detect calls when Zoom/Teams starts
    prompt_on_call: bool = True      # Explicit user consent popup before recording a detected call
    notetaker_auto_start_banner_dismissed: bool = False  # Auto-start prompt banner dismissal on Notetaker page
    record_meeting_screen: bool = True # Read-AI style: record screen video (slides, video, code) at low fps
    meeting_recording_mode: str = "audio_video"  # "audio_only" | "audio_video" (persisted last-used mode)
    screen_fps: int = 15              # 15 fps default (smooth motion, user-selectable 10-60 fps with file size estimates)
    meeting_mic_device_index: int | None = None
    meeting_mic_device_name: str = ""
    meeting_mic_gain: float = 1.0    # Meeting capture gain (1.0 = pristine unity gain; studio DSP AGC normalizes speech to broadcast target)
    loopback_device_index: int | None = None  # None = all outputs
    live_meeting_transcript: bool = False
    timezone: str = "local"

    # Desktop & Flow Bar
    launch_at_login: bool = True
    start_hidden: bool = True        # on login: only floating Flow Bar + tray
    show_floating_icon: bool = True
    icon_opacity: float = 0.92
    icon_x: int = -1
    icon_y: int = -1
    first_run_done: bool = False
    onboarding_completed: bool = False
    theme: str = "light"             # "light" | "dark" | "system"
    reduced_motion: bool = False     # Accessibility: disable UI transitions and continuous waveform animations
    flow_bar_edge: str = "right"     # "bottom" | "left" | "right" (default right edge vertically centered)
    flow_bar_y_ratio: float = 0.5    # vertical position ratio (0.1 to 0.9)
    flow_bar_hidden_until: float = 0.0
    reminder_muted_until: float = 0.0

    # Writing Style & Cleanup Intensity
    dictation_writing_mode: str = "clean"
    auto_cleanup_level: str = "light"  # "none" | "light" | "medium" | "high"
    style_work: str = "formal"         # "formal" | "casual" | "very_casual" | "excited"
    style_email: str = "formal"
    style_personal: str = "casual"
    style_other: str = "formal"

    # Shortcuts & Gestures
    hands_free_double_tap: bool = True
    notetaker_triple_tap: bool = False
    command_hotkey: str = "ctrl+win+space"
    scratchpad_hotkey: str = "ctrl+shift+n"
    transform_hotkey: str = "ctrl+shift+t"
    paste_last_hotkey: str = "ctrl+shift+v"
    copy_last_hotkey: str = "ctrl+shift+c"

    # Dictation Behavior & Scratchpad
    hinglish_enabled: bool = True
    auto_add_dictionary: bool = True
    smart_formatting: bool = True
    sound_effects: bool = True
    mute_audio_while_dictating: bool = False
    email_auto_signature: bool = False
    email_signature_text: str = ""
    scratchpad_in_flow_bar: bool = True
    scratchpad_open_behavior: str = "last_note"  # "last_note" | "new_note"

    # Notetaker & Calendar Automation
    notetaker_auto_end: bool = True
    notetaker_auto_open_summary: bool = True
    notetaker_notepad_on_start: str = "open"     # "open" | "split" | "hidden"
    notetaker_max_length_min: int = 120
    notetaker_auto_detect_speakers: bool = True
    notetaker_live_transcript: bool = False
    notetaker_library_width: int = 290
    notetaker_library_collapsed: bool = False
    notetaker_library_compact: bool = False
    notetaker_ask_rail_open: bool = False
    notetaker_split_mode: str = "off"
    calendar_reminder_lead_min: int = 1
    calendar_provider: str = "none"
    calendar_account_email: str = ""
    calendar_client_id: str = ""
    calendar_tenant_id: str = "common"
    calendar_ics_url: str = ""
    google_calendar_client_id: str = ""
    google_calendar_client_secret: str = ""
    google_calendar_account_email: str = ""
    google_calendar_enabled: bool = False
    zoom_client_id: str = ""
    zoom_client_secret: str = ""
    zoom_account_email: str = ""
    zoom_enabled: bool = False
    ms_graph_enabled: bool = False
    calendar_sync_interval_sec: int = 60
    calendar_sync_horizon_days: int = 30
    calendar_auto_join: bool = False
    hide_in_screen_share: bool = False
    current_user_id: str = "default"
    auth_backend_url: str = ""

    # Context & Vibe Coding
    context_awareness: bool = True
    vibe_coding_variable_recognition: bool = True
    vibe_coding_file_tagging: bool = True

    # Advanced / Internal Flags
    dictation_mode: str = "toggle"
    ptt_hotkey: str = "ctrl+win"
    dictation_cleanup: bool = True
    dictation_include_system_audio: bool = False
    insertion_method: str = "auto"
    restore_clipboard: bool = True
    smart_leading_space: bool = True
    vad_filter: bool = True
    meeting_capture_mode: str = "mixed"
    preserve_separate_tracks: bool = True
    progressive_transcription: bool = False
    progressive_interval_sec: int = 15
    auto_summarize_meeting: bool = True
    compute_type: str = "default"
    loopback_device_name: str = ""
    widget_expanded: bool = False
    reports_library_width: int = 340

    @property
    def dictation_hotkey(self) -> str:
        return self.typing_hotkey

    @dictation_hotkey.setter
    def dictation_hotkey(self, val: str) -> None:
        self.typing_hotkey = val

    @property
    def start_minimized_to_tray(self) -> bool:
        return self.start_hidden

    @start_minimized_to_tray.setter
    def start_minimized_to_tray(self, val: bool) -> None:
        self.start_hidden = val

    @property
    def widget_opacity(self) -> float:
        return self.icon_opacity

    @widget_opacity.setter
    def widget_opacity(self, val: float) -> None:
        self.icon_opacity = val

    @property
    def show_floating_widget(self) -> bool:
        return self.show_floating_icon

    @show_floating_widget.setter
    def show_floating_widget(self, val: bool) -> None:
        self.show_floating_icon = val

    @property
    def widget_x(self) -> int:
        return self.icon_x

    @widget_x.setter
    def widget_x(self, val: int) -> None:
        self.icon_x = val

    @property
    def widget_y(self) -> int:
        return self.icon_y

    @widget_y.setter
    def widget_y(self, val: int) -> None:
        self.icon_y = val

    def __post_init__(self) -> None:
        if not self.storage_dir:
            self.storage_dir = str(get_user_data_root())
        if not self.models_dir:
            if self.storage_dir == str(get_user_data_root()):
                self.models_dir = str(get_user_models_root())
            else:
                self.models_dir = str(Path(self.storage_dir) / "models")
        self.ensure_directories()

    def ensure_directories(self) -> None:
        base = Path(self.storage_dir)
        for sub in ("recordings", "exports", "logs", "crash_reports", "backups", "updates", "temp"):
            os.makedirs(base / sub, exist_ok=True)
        os.makedirs(self.models_dir, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return Path(self.storage_dir) / "voxlocal.db"

    @property
    def recordings_dir(self) -> Path:
        return Path(self.storage_dir) / "recordings"

    @property
    def exports_dir(self) -> Path:
        return Path(self.storage_dir) / "exports"

    @property
    def llm_models_dir(self) -> Path:
        return Path(self.models_dir) / "meeting_llm"

    @property
    def llm_model_path(self) -> Path:
        return self.llm_models_dir / self.llm_model

    @property
    def meeting_asr_dir(self) -> Path:
        return Path(self.models_dir) / "meeting_asr" / self.meeting_asr_model

    @property
    def meeting_diarization_dir(self) -> Path:
        return Path(self.models_dir) / "meeting_diarization" / "sherpa-diarization"

    @property
    def meeting_llm_path(self) -> Path:
        return self.llm_models_dir / self.llm_model

    @property
    def meeting_embeddings_dir(self) -> Path:
        return Path(self.models_dir) / "meeting_embeddings" / "bge-small-en-v1.5-onnx-Q"

    @classmethod
    def default_config_path(cls) -> Path:
        custom_env = os.environ.get("VOXLOCAL_CONFIG_PATH")
        if custom_env:
            return Path(custom_env)
        return get_user_data_root() / "config.json"

    def config_file_path(self) -> Path:
        custom_env = os.environ.get("VOXLOCAL_CONFIG_PATH")
        if custom_env:
            return Path(custom_env)
        if self.storage_dir and any(
            tok in str(self.storage_dir).lower()
            for tok in ("temp", "tmp", "test_", "pytest", "scratch")
        ):
            return Path(self.storage_dir) / "config.json"
        return get_user_data_root() / "config.json"

    def save(self) -> None:
        try:
            path = self.config_file_path()
            os.makedirs(path.parent, exist_ok=True)
            data = asdict(self)
            # Ensure Path instances are serialized cleanly as strings
            for k, v in list(data.items()):
                if isinstance(v, Path):
                    data[k] = str(v)
            # Never serialize client secrets to plaintext config.json
            data["google_calendar_client_secret"] = ""
            data["zoom_client_secret"] = ""
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, path)  # atomic: never leaves a half-written config
        except Exception as exc:
            logger.error("Failed to save config: %s", exc)

    @classmethod
    def load(cls) -> "AppConfig":
        path = cls.default_config_path()
        if path.exists():
            try:
                data: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
                valid = set(cls.__dataclass_fields__)
                cfg = cls(**{k: v for k, v in data.items() if k in valid})
                # Enforce v2.0 single-winner post-call architecture on migrated configs
                if not cfg.llm_model or "Llama-3.2-1B" in cfg.llm_model:
                    cfg.llm_model = "Qwen3.5-4B-Q4_K_M.gguf"
                if not cfg.meeting_asr_model:
                    cfg.meeting_asr_model = "cohere-transcribe-en-int8"
                cfg.live_meeting_transcript = False
                cfg.notetaker_live_transcript = False
                cfg.live_preview_enabled = False
                cfg.progressive_transcription = False
                cfg.enable_phase2_asr_pipeline = False
                cfg.asr_session_mode = "english"
                cfg.live_english_backend = "disabled"
                cfg.live_hinglish_backend = "disabled"
                cfg.offline_english_backend = "cohere_transcribe"
                cfg.offline_hinglish_backend = "disabled"
                cfg.selective_enable_hinglish_cue_routing = False
                if not cfg.timezone or str(cfg.timezone).strip().upper() == "UTC":
                    cfg.timezone = "local"
                # Self-heal if primary data/config.json was previously polluted by a test_mtg_* storage_dir
                if not os.environ.get("VOXLOCAL_CONFIG_PATH") and cfg.storage_dir:
                    s_low = str(cfg.storage_dir).lower()
                    if any(tok in s_low for tok in ("temp", "tmp", "test_", "pytest", "scratch")):
                        cfg.storage_dir = str(get_user_data_root())
                        cfg.save()
                return cfg
            except Exception as exc:
                logger.warning("config.json unreadable, using defaults: %s", exc)
        cfg = cls()
        cfg.save()
        return cfg
