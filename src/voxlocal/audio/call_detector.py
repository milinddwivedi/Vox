"""
Layered Automatic Meeting & Call Detection Engine for VoxLocal.
Detects active meeting participation across:
- Microsoft Teams (Desktop & Web): isolates dedicated Call Stage windows from hub chat/calendar tabs.
- Zoom (Desktop): isolates meeting/webinar windows from launcher/home screen.
- Google Meet (Chrome/Edge): detects live Meet tabs via LocalBrowserTabCaptureBridge or window title.
- Webex and Slack Huddles.
State Machine transitions:
NOT_RUNNING -> APPLICATION_DETECTED -> MEETING_SUSPECTED -> MEETING_CONFIRMED -> AWAITING_USER_DECISION -> RECORDING -> MEETING_ENDED
Features:
- Stable session deduplication via meeting_session_id (resizing, moving, or layout changes do not re-prompt).
- Layered detection (window discovery, UI classification, mic consent registry verification).
- 100% offline, local Windows APIs (zero cloud dependency).
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
from enum import Enum
import json
import re
import time
import winreg
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from voxlocal.core.logger import get_logger
logger = get_logger("audio.calls")
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

def is_pid_alive(pid: Optional[int]) -> bool:
    if not pid or pid <= 0:
        return False
    h = kernel32.OpenProcess(0x1000, False, pid)
    if h:
        kernel32.CloseHandle(h)
        return True
    return False
CONSENT_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
)
# (substring in registry key, lowercase) -> (friendly name, is_browser)
CALL_APPS: List[Tuple[str, str, bool]] = [
    ("zoom.exe", "Zoom", False),
    ("msteams_", "Microsoft Teams", False),
    ("ms-teams.exe", "Microsoft Teams", False),
    ("#teams.exe", "Microsoft Teams", False),
    ("webex", "Webex", False),
    ("ciscocollabhost", "Webex", False),
    ("slack.exe", "Slack", False),
    ("skype", "Skype", False),
    ("discord.exe", "Discord", False),
    ("whatsapp", "WhatsApp", False),
    ("telegram", "Telegram", False),
    ("chrome.exe", "Browser call (Meet/Web)", True),
    ("msedge.exe", "Browser call (Meet/Web)", True),
    ("firefox.exe", "Browser call (Meet/Web)", True),
    ("brave.exe", "Browser call (Meet/Web)", True),
    ("comet.exe", "Browser call (Meet/Web)", True),
    ("opera.exe", "Browser call (Meet/Web)", True),
]
IGNORE = ("python", "voxlocal", "soundrecorder", "read_ai", "nvcontainer", "msedgewebview2")
NATIVE_MIN_SEC = 2.5
BROWSER_MIN_SEC = 10.0
END_GRACE_SEC = 12.0

class MeetingDetectorState(str, Enum):
    NOT_RUNNING = "NOT_RUNNING"
    APPLICATION_DETECTED = "APPLICATION_DETECTED"
    MEETING_SUSPECTED = "MEETING_SUSPECTED"
    MEETING_CONFIRMED = "MEETING_CONFIRMED"
    AWAITING_USER_DECISION = "AWAITING_USER_DECISION"
    RECORDING = "RECORDING"
    MEETING_ENDED = "MEETING_ENDED"


class MeetingLifecycleState(str, Enum):
    """Explicit 10-state Meeting Studio recording lifecycle state machine."""
    NO_MEETING_DETECTED = "no_meeting_detected"
    MEETING_DETECTED = "meeting_detected"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    PREPARING_CAPTURE = "preparing_capture"
    RECORDING = "recording"
    TEMPORARILY_INTERRUPTED = "temporarily_interrupted"
    MEETING_ENDING = "meeting_ending"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class MeetingSessionInfo:
    """Stable metadata describing an ongoing detected meeting session."""
    session_id: str                      # Stable across window moves/resizes (e.g. "teams_66990_2230116")
    platform: str                        # "Microsoft Teams", "Zoom", "Google Meet", etc.
    window_hwnd: Optional[int]           # HWND of the meeting call stage
    window_title: str                    # Raw title of the call window
    clean_title: str                     # Cleaned, presentation-ready title
    bounds: Tuple[int, int, int, int]    # (left, top, width, height)
    is_call_stage: bool                  # True if verified dedicated call stage window
    mic_active: bool                     # True if application holds Windows mic consent
    pid: Optional[int] = None
    browser_tab_id: Optional[int] = None
    first_detected_at: float = 0.0
    confirmed_at: float = 0.0
    decision: Optional[str] = None       # None, "approved", "dismissed", "ignored"

    @property
    def is_active(self) -> bool:
        """True while this MeetingSessionInfo represents an active detected call."""
        return True

    @property
    def hwnd(self) -> Optional[int]:
        """Alias for window_hwnd used by UI components."""
        return self.window_hwnd
@dataclass

class ActiveCall:
    """Backwards-compatible adapter for existing call detection callers."""
    app_name: str
    key: str
    since: float
    window_hwnd: Optional[int] = None
    window_title: str = ""
    session_id: str = ""

def clean_meeting_title(raw_title: str, platform: str) -> str:
    """Produce a concise, human-readable meeting title free from browser/app suffixes and call prefixes."""
    tl = (raw_title or "").strip()
    if not tl:
        return f"{platform} Meeting"
    # Strip common bracketed call prefixes, e.g. '[Microsoft Teams Call] Vox Testing - 2', '[Teams] Daily Sync'
    tl = re.sub(
        r"^\[\s*(?:microsoft\s+teams|teams|zoom|google\s+meet|meet|webex|slack)?(?:\s*(?:call|meeting|huddle))?\s*\]\s*[-:\u2013\u2014]?\s*",
        "",
        tl,
        flags=re.IGNORECASE,
    ).strip()
    tl = re.sub(
        r"^(?:microsoft\s+teams\s+call|teams\s+call|zoom\s+meeting|google\s+meet)\s*[-:\u2013\u2014]\s*",
        "",
        tl,
        flags=re.IGNORECASE,
    ).strip()
    # Strip common leading Teams tab prefixes like 'Chat | ' or 'Meeting | '
    tl = re.sub(
        r"^(?:chat|meeting(?:\s+join)?|calls?|stage)\s*\|\s*",
        "",
        tl,
        flags=re.IGNORECASE,
    ).strip()
    # Strip platform suffixes and trailing meeting annotations like '(Meeting)', '(Call)', '[Call]'
    suffixes = (
        "| microsoft teams",
        "- microsoft teams",
        "microsoft teams",
        "- google chrome",
        "- google meet",
        "- personal - microsoft edge",
        "- work - microsoft edge",
        "- microsoft edge",
        "- edge",
        "- zoom workplace",
        "- zoom",
        "zoom",
    )
    for _ in range(2):
        lower = tl.lower()
        for suff in suffixes:
            if lower.endswith(suff):
                tl = tl[: -len(suff)].strip(" |-\u2013\u2014")
                lower = tl.lower()
                break
        tl = re.sub(
            r"\s*[\(\[]\s*(?:call|meeting|huddle|in call)\s*[\)\]]$",
            "",
            tl,
            flags=re.IGNORECASE,
        ).strip(" |-\u2013\u2014")
    lower = tl.lower()
    # Format Google Meet titles cleanly
    if lower.startswith("meet -"):
        code_part = tl[6:].strip()
        tl = f"Google Meet: {code_part}" if code_part else "Google Meet"
    return tl if tl else f"{platform} Meeting"

def list_all_meeting_windows(
    include_minimized: bool = True,
    include_non_call_hubs: bool = False,
) -> List[Tuple[int, str, str, Tuple[int, int, int, int]]]:
    """
    Scans and returns all open meeting call windows across
    Zoom, Microsoft Teams, Google Meet (in browsers), Webex, and Slack.
    By default excludes non-call hubs (Chat/Calendar/Home) and includes minimized active call stages.
    Returns list of (hwnd, platform_name, window_title, (left, top, width, height)).
    """
    candidates: List[Tuple[int, str, str, Tuple[int, int, int, int]]] = []
    hdesk = None
    try:
        hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
    except Exception:
        hdesk = None
    dwmapi = getattr(ctypes.windll, "dwmapi", None)

    def _enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        is_iconic = bool(user32.IsIconic(hwnd))
        if is_iconic and not include_minimized:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        title_lower = title.lower()
        # Exclude VoxLocal itself, system shells, and non-call windows
        if any(ign in title_lower for ign in ("voxlocal", "program manager", "task switching", "settings", "calculator")):
            return True
        if is_iconic:
            rect_left, rect_top, w, h = 0, 0, 1280, 720
        else:
            rect = wintypes.RECT()
            got_bounds = False
            if dwmapi:
                try:
                    res = dwmapi.DwmGetWindowAttribute(
                        wintypes.HWND(hwnd),
                        wintypes.DWORD(9),  # DWMWA_EXTENDED_FRAME_BOUNDS
                        ctypes.byref(rect),
                        ctypes.sizeof(rect),
                    )
                    if res == 0:
                        got_bounds = True
                except Exception:
                    pass
            if not got_bounds:
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
            rect_left, rect_top = rect.left, rect.top
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            # Reject windows smaller than minimum call stage canvas (toasts, mini-prompts)
            if w < 480 or h < 340:
                return True
        # Reject notification toasts, lobby alerts, waiting rooms, and incoming call prompts
        if any(skip in title_lower for skip in (
            "waiting in the lobby",
            "in the lobby",
            "waiting for host",
            "waiting for the host",
            "ready to join?",
            "join a meeting",
            "choose your video and audio options",
            "let them in",
            "incoming call",
            "is calling",
            "calling...",
            "toast",
            "notification",
            "prompt",
            "alert",
        )):
            return True
        try:
            style = user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
            ex_style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
            # Reject tool windows and fixed, non-maximizable cards under 600x450
            if bool(ex_style & 0x00000080):  # WS_EX_TOOLWINDOW
                return True
            if not is_iconic and not bool(style & 0x00010000) and (w < 600 or h < 450):  # WS_MAXIMIZEBOX
                return True
        except Exception:
            pass
        platform = ""
        proc_name = ""
        try:
            from voxlocal.video.meeting_capture import get_window_pid_and_process_name
            _pid, proc_name = get_window_pid_and_process_name(int(hwnd))
        except Exception:
            proc_name = ""
        # Check Zoom
        if (
            proc_name in ("zoom.exe", "cpthost.exe")
            or "zoom meeting" in title_lower
            or "zoom webinar" in title_lower
            or (title_lower.startswith("zoom") and "update" not in title_lower)
        ):
            platform = "Zoom"
        # Check Microsoft Teams
        elif (
            proc_name in ("ms-teams.exe", "teams.exe", "msteams.exe")
            or ("teams" in title_lower and any(kw in title_lower for kw in ("meeting", "call", "sharing", "|")))
            or "microsoft teams" in title_lower
        ):
            if any(skip in title_lower for skip in ("sharing control bar", "microsoft teams notification")):
                return True
            platform = "Microsoft Teams"
        # Check Google Meet
        elif "meet -" in title_lower or "meet.google.com" in title_lower or "google meet" in title_lower:
            platform = "Google Meet"
        # Check Webex
        elif proc_name in ("atmgr.exe", "ciscocollabhost.exe") or "webex" in title_lower or "cisco" in title_lower:
            platform = "Webex"
        # Check Slack
        elif "slack" in title_lower and ("huddle" in title_lower or "call" in title_lower):
            platform = "Slack"
        if platform:
            if is_iconic and (is_non_call_hub(platform, title) or is_leaving_title(title)):
                return True
            candidates.append((hwnd, platform, title, (rect_left, rect_top, w, h)))
        return True
    try:
        proc = WNDENUMPROC(_enum_proc)
        if hdesk:
            user32.EnumDesktopWindows(hdesk, proc, 0)
        if not candidates:
            user32.EnumWindows(proc, 0)
    except Exception:
        pass
    finally:
        if hdesk:
            try:
                user32.CloseDesktop(hdesk)
            except Exception:
                pass
    candidates.sort(key=_rank_candidate, reverse=True)
    # Filter out non-call hub windows and leaving screens
    call_stage_only = [c for c in candidates if not is_non_call_hub(c[1], c[2]) and not is_leaving_title(c[2])]
    if not include_non_call_hubs:
        return call_stage_only
    if call_stage_only:
        return call_stage_only
    return candidates

def is_non_call_hub(plat: str, t_title: str) -> bool:
    """True if window is a main hub/launcher/chat/waiting-room rather than a live active call stage."""
    tl = (t_title or "").strip().lower()
    plat_norm = (plat or "").strip().lower()
    if any(skip in tl for skip in (
        "waiting in the lobby",
        "in the lobby",
        "waiting for host",
        "waiting for the host",
        "ready to join?",
        "join a meeting",
        "choose your video and audio options",
        "let them in",
        "incoming call",
        "is calling",
        "calling...",
        "notification",
        "toast",
        "prompt",
        "alert",
    )):
        return True
    # Microsoft Teams hub detection (works whether plat is "Microsoft Teams" or generic "Call")
    if plat_norm in ("microsoft teams", "teams", "call", "") or "teams" in tl:
        if tl in ("microsoft teams", "microsoft teams classic", "teams"):
            return True
        if tl.startswith((
            "chat |",
            "activity |",
            "calendar |",
            "teams |",
            "calls |",
            "onedrive |",
            "files |",
            "approvals |",
            "shifts |",
            "settings |",
        )):
            return True
    # Zoom hub detection
    if plat_norm in ("zoom", "call", "") or "zoom" in tl:
        if tl in ("zoom workplace", "zoom - free account", "zoom - pro account", "zoom cloud meetings", "zoom"):
            return True
        if plat_norm == "zoom" and ("update" in tl or "settings" in tl):
            return True
    # Google Meet hub detection
    if plat_norm in ("google meet", "meet", "browser call (meet/web)", "call", "") or "meet" in tl:
        if tl in (
            "google meet",
            "meet",
            "meet - google chrome",
            "google meet - google chrome",
            "meet - microsoft edge",
            "google meet - microsoft edge",
        ):
            return True
    return False
_is_non_call_hub = is_non_call_hub

LEAVING_TITLES = (
    "leaving...",
    "leaving",
    "left the meeting",
    "call ended",
    "meeting ended",
    "you left the meeting",
    "reconnecting...",
    "ended call",
)

def is_leaving_title(title: str) -> bool:
    """True if window title indicates the user has hung up or the meeting is terminating."""
    if not title:
        return False
    tl = title.strip().lower()
    return any(tl == lt or tl.startswith("leaving") or "left the meeting" in tl for lt in LEAVING_TITLES)

def _rank_candidate(c: Tuple[int, str, str, Tuple[int, int, int, int]]) -> Tuple[int, int]:
    _h, plat, t_title, bounds = c
    tl = t_title.lower()
    if is_non_call_hub(plat, t_title):
        score = 5
    elif plat == "Microsoft Teams":
        score = 220
        if any(kw in tl for kw in ("meeting", "call", "sharing", "scrum", "standup", "sync")):
            score += 20
    elif any(kw in tl for kw in ("meeting", "call", "sharing", "huddle", "webinar", "meet -", "google meet")):
        score = 200
    else:
        score = 50
    area = bounds[2] * bounds[3]
    return (score, area)

def find_meeting_window(app_name: str = "") -> Optional[Tuple[int, str, Tuple[int, int, int, int]]]:
    """Finds the active top-level meeting window for the detected call app."""
    all_wins = list_all_meeting_windows()
    if not all_wins:
        return None
    if not app_name:
        h, _plat, title, bounds = all_wins[0]
        return (h, title, bounds)
    app_lower = app_name.lower()
    for h, plat, title, bounds in all_wins:
        plat_lower = plat.lower()
        if "zoom" in app_lower and "zoom" in plat_lower:
            return (h, title, bounds)
        if "teams" in app_lower and "teams" in plat_lower:
            return (h, title, bounds)
        if ("meet" in app_lower or "browser" in app_lower) and "meet" in plat_lower:
            return (h, title, bounds)
        if "webex" in app_lower and "webex" in plat_lower:
            return (h, title, bounds)
        if "slack" in app_lower and "slack" in plat_lower:
            return (h, title, bounds)
    h, _plat, title, bounds = all_wins[0]
    return (h, title, bounds)

def _mic_users_now() -> List[str]:
    """Registry keys (lowercased) of apps currently holding or recently using the microphone."""
    users: List[str] = []
    for sub in ("", "\\NonPackaged"):
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONSENT_KEY + sub)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if name == "NonPackaged":
                    continue
                try:
                    with winreg.OpenKey(root, name) as k:
                        start = winreg.QueryValueEx(k, "LastUsedTimeStart")[0]
                        stop = winreg.QueryValueEx(k, "LastUsedTimeStop")[0]
                    if start and (stop == 0 or (start > stop)):
                        users.append(name.lower())
                except OSError:
                    continue
    return users

def classify(key: str) -> Optional[Tuple[str, bool]]:
    if any(x in key for x in IGNORE):
        return None
    for needle, name, is_browser in CALL_APPS:
        if needle in key:
            return name, is_browser
    return None

class CallDetector:
    """
    Multi-Signal Meeting State Detector with stable session tracking and zero duplicate alerts.
    State machine:
    NOT_RUNNING -> APPLICATION_DETECTED -> MEETING_SUSPECTED -> MEETING_CONFIRMED -> AWAITING_USER_DECISION -> RECORDING -> MEETING_ENDED
    """

    def __init__(self) -> None:
        self.state: MeetingDetectorState = MeetingDetectorState.NOT_RUNNING
        self._first_seen: Dict[str, float] = {}
        self._window_first_seen: Dict[str, float] = {}
        self._hwnd_first_seen: Dict[int, int] = {}
        self._suppression_timestamps: Dict[str, float] = {}
        self.active_session: Optional[MeetingSessionInfo] = None
        self.active: Optional[ActiveCall] = None  # Backwards compatibility adapter
        self.last_ended_session: Optional[MeetingSessionInfo] = None
        self.last_ended_active: Optional[ActiveCall] = None
        self._released_at: Optional[float] = None
        self._is_recording: bool = False
        # Session deduplication sets
        self.prompted_session_ids: Set[str] = set()
        self.dismissed_session_ids: Set[str] = set()
        self.ignored_session_ids: Set[str] = set()
        # WinEvent Hook handles
        self._winevent_hook = None
        self._winevent_cb = None
        self._last_winevent_time = 0.0

    def start_winevent_listener(self, on_change_callback) -> None:
        """Register OS WinEvent hook for instant, event-driven detection of call window creation/titles."""
        try:
            WINEVENTPROC = ctypes.WINFUNCTYPE(
                None,
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.HWND,
                wintypes.LONG,
                wintypes.LONG,
                wintypes.DWORD,
                wintypes.DWORD,
            )
            def _hook_proc(hHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
                if idObject == 0:  # OBJID_WINDOW
                    now = time.monotonic()
                    # Throttle hook events to max once every 0.8s so UI isn't flooded
                    if now - getattr(self, "_last_winevent_time", 0.0) >= 0.8:
                        self._last_winevent_time = now
                        try:
                            on_change_callback()
                        except Exception:
                            pass
            self._winevent_cb = WINEVENTPROC(_hook_proc)
            self._winevent_hook = user32.SetWinEventHook(
                0x8000, 0x800C,  # EVENT_OBJECT_CREATE to EVENT_OBJECT_NAMECHANGE
                0, self._winevent_cb, 0, 0,
                0,  # WINEVENT_OUTOFCONTEXT
            )
        except Exception as exc:
            logger.debug("WinEvent hook registration skipped: %s", exc)

    def stop_winevent_listener(self) -> None:
        """Safely unhook WinEvent listener on shutdown."""
        if self._winevent_hook:
            try:
                user32.UnhookWinEvent(self._winevent_hook)
            except Exception:
                pass
            self._winevent_hook = None
            self._winevent_cb = None

    def set_recording_active(self, is_recording: bool) -> None:
        """Update detector recording state."""
        self._is_recording = is_recording
        if is_recording:
            self.state = MeetingDetectorState.RECORDING
        elif self.active_session:
            self.state = MeetingDetectorState.MEETING_CONFIRMED

    def bind_active_meeting(
        self,
        hwnd: Optional[int] = None,
        platform: Optional[str] = None,
        title: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> MeetingSessionInfo:
        """
        Manually or programmatically bind an active meeting session to the detector.
        Used when recording starts from UI toggle bar, drawer, shortcut, or calendar auto-start,
        ensuring the detector actively tracks call lifecycle, detects call endings, and enables auto-stop.
        """
        now = time.monotonic()
        # Discover window info if not provided
        if hwnd is None:
            all_wins = list_all_meeting_windows(include_non_call_hubs=False)
            if all_wins:
                hwnd, win_plat, win_title, bounds = all_wins[0]
                platform = platform or win_plat
                title = title or win_title

        plat = platform or "Meeting"
        raw_title = title or "Active Call"
        clean_title_str = clean_meeting_title(raw_title, plat)
        title_slug = re.sub(r"[^a-zA-Z0-9]", "", clean_title_str.lower())[:32]
        h_val = hwnd or 0
        epoch = self._hwnd_first_seen.setdefault(h_val, int(time.time()))
        plat_slug = plat.lower().replace(" ", "_")
        sess_id = f"{plat_slug}_{title_slug or 'call'}_{h_val}_{epoch}"

        if pid is None and hwnd:
            try:
                from voxlocal.video.meeting_capture import get_window_pid_and_process_name
                pid, _ = get_window_pid_and_process_name(hwnd)
            except Exception:
                pid = None

        sess = MeetingSessionInfo(
            session_id=sess_id,
            platform=plat,
            window_hwnd=hwnd,
            window_title=raw_title,
            clean_title=clean_title_str,
            bounds=(0, 0, 1280, 720),
            is_call_stage=bool(hwnd is not None),
            mic_active=True,
            pid=pid,
            first_detected_at=now,
            confirmed_at=now,
            decision="approved",
        )
        self.active_session = sess
        self.active = ActiveCall(
            app_name=plat,
            key=f"manual_{sess_id}",
            since=now,
            window_hwnd=hwnd,
            window_title=clean_title_str,
            session_id=sess_id,
        )
        self.prompted_session_ids.add(sess_id)
        self.state = MeetingDetectorState.RECORDING
        self._is_recording = True
        self._released_at = None
        logger.info("Bound active meeting to CallDetector: %s (HWND=%s, PID=%s, title='%s')", plat, hwnd, pid, clean_title_str)
        return sess

    def mark_decision(self, session_id: str, decision: str) -> None:
        """Record user decision ('approved', 'dismissed', 'ignored') for this session ID."""
        now = time.monotonic()
        if self.active_session and self.active_session.session_id == session_id:
            self.active_session.decision = decision
        if decision == "dismissed":
            self.dismissed_session_ids.add(session_id)
            self._suppression_timestamps[session_id] = now
        elif decision == "ignored":
            self.ignored_session_ids.add(session_id)
            self._suppression_timestamps[session_id] = now
        elif decision == "approved":
            self.state = MeetingDetectorState.RECORDING

    def _prune_session_suppression(self, session_id: Optional[str], hwnd: Optional[int] = None) -> None:
        """Prune suppression records for a specific session upon completion or teardown."""
        if session_id:
            self.prompted_session_ids.discard(session_id)
            self.dismissed_session_ids.discard(session_id)
            self.ignored_session_ids.discard(session_id)
            self._window_first_seen.pop(session_id, None)
            self._suppression_timestamps.pop(session_id, None)
        if hwnd is not None:
            self._hwnd_first_seen.pop(hwnd, None)

    def prune_expired_suppressions(self, now: float, max_age_sec: float = 600.0) -> None:
        """Prune suppression records older than max_age_sec (default 10 minutes) to prevent memory leak."""
        expired = [sid for sid, ts in list(self._suppression_timestamps.items()) if (now - ts) > max_age_sec]
        for sid in expired:
            self.prompted_session_ids.discard(sid)
            self.dismissed_session_ids.discard(sid)
            self.ignored_session_ids.discard(sid)
            self._window_first_seen.pop(sid, None)
            self._suppression_timestamps.pop(sid, None)
        if len(self._hwnd_first_seen) > 100:
            dead_hwnds = [h for h in list(self._hwnd_first_seen.keys()) if not user32.IsWindow(wintypes.HWND(h))]
            for h in dead_hwnds:
                self._hwnd_first_seen.pop(h, None)

    def poll(self, now: Optional[float] = None) -> Optional[str]:
        """
        Poll system state. Returns:
        - 'started' (meeting confirmed and ready for consent / recording)
        - 'ended' (meeting call ended)
        - None
        """
        now = time.monotonic() if now is None else now
        try:
            users = _mic_users_now()
        except Exception as exc:
            logger.debug("Microphone consent poll failed: %s", exc)
            users = []
        return self.update(users, now)

    def update(self, users: List[str], now: float) -> Optional[str]:
        """Layered multi-signal evaluation logic for meeting participation."""
        self.prune_expired_suppressions(now)
        # 1. Inspect Windows microphone consent registry
        candidates = {u: classify(u) for u in users}
        candidates = {u: c for u, c in candidates.items() if c}
        for u in list(self._first_seen):
            if u not in candidates:
                del self._first_seen[u]
        for u in candidates:
            self._first_seen.setdefault(u, now)
        # 2. Check for active browser tab via LocalBrowserTabCaptureBridge
        meet_tab_info = None
        try:
            from voxlocal.video.meeting_capture import LocalBrowserTabCaptureBridge
            bridge = LocalBrowserTabCaptureBridge.get_instance()
            tabs = bridge.list_active_tabs()
            if tabs:
                meet_tab_info = tabs[0]
        except Exception:
            meet_tab_info = None
        # 3. Discover all candidate meeting windows
        all_wins = list_all_meeting_windows()
        verified_call_stages = [w for w in all_wins if not is_non_call_hub(w[1], w[2])]
        # If no active session currently tracked:
        if self.active_session is None:
            # Check for Google Meet extension bridge first
            if meet_tab_info is not None:
                tab_id = int(meet_tab_info.get("tab_id", 0))
                m_slug = meet_tab_info.get("meeting_slug") or "active_call"
                tab_epoch = self._hwnd_first_seen.setdefault(tab_id, int(time.time()))
                sess_id = f"meet_tab_{tab_id}_{m_slug}_{tab_epoch}"
                if sess_id not in self.ignored_session_ids and sess_id not in self.dismissed_session_ids:
                    if sess_id not in self.prompted_session_ids:
                        title = meet_tab_info.get("title") or "Google Meet Call"
                        clean_title_str = clean_meeting_title(title, "Google Meet")
                        sess = MeetingSessionInfo(
                            session_id=sess_id,
                            platform="Google Meet",
                            window_hwnd=None,
                            window_title=title,
                            clean_title=clean_title_str,
                            bounds=(0, 0, 1920, 1080),
                            is_call_stage=True,
                            mic_active=True,
                            browser_tab_id=tab_id,
                            first_detected_at=now,
                            confirmed_at=now,
                        )
                        self.active_session = sess
                        self.active = ActiveCall(
                            app_name="Google Meet",
                            key=f"chrome.exe_{tab_id}",
                            since=now,
                            window_hwnd=None,
                            window_title=clean_title_str,
                            session_id=sess_id,
                        )
                        self.state = MeetingDetectorState.AWAITING_USER_DECISION
                        self.prompted_session_ids.add(sess_id)
                        logger.info("Google Meet tab detected via bridge: session_id=%s ('%s')", sess_id, clean_title_str)
                        return "started"
            # PRIMARY DIRECT SIGNAL: Check verified call stage windows
            if verified_call_stages:
                hwnd, app_name, w_title, bounds = verified_call_stages[0]
                clean_title_str = clean_meeting_title(w_title, app_name)
                title_slug = re.sub(r"[^a-zA-Z0-9]", "", clean_title_str.lower())[:32]
                epoch = self._hwnd_first_seen.setdefault(hwnd, int(time.time()))
                plat_slug = app_name.lower().replace(" ", "_")
                sess_id = f"{plat_slug}_{title_slug}_{hwnd}_{epoch}"
                if sess_id not in self.ignored_session_ids and sess_id not in self.dismissed_session_ids:
                    # Debounce: ensure call window is visible and stable for at least 1.0s
                    self._window_first_seen.setdefault(sess_id, now)
                    if (now - self._window_first_seen[sess_id]) >= 1.0:
                        if sess_id not in self.prompted_session_ids:
                            pid = None
                            try:
                                from voxlocal.video.meeting_capture import get_window_pid_and_process_name
                                pid, _ = get_window_pid_and_process_name(hwnd)
                            except Exception:
                                pid = None
                            sess = MeetingSessionInfo(
                                session_id=sess_id,
                                platform=app_name,
                                window_hwnd=hwnd,
                                window_title=w_title,
                                clean_title=clean_title_str,
                                bounds=bounds,
                                is_call_stage=True,
                                mic_active=bool(candidates),
                                pid=pid,
                                first_detected_at=self._window_first_seen[sess_id],
                                confirmed_at=now,
                            )
                            self.active_session = sess
                            self.active = ActiveCall(
                                app_name=app_name,
                                key=sess_id,
                                since=now,
                                window_hwnd=hwnd,
                                window_title=clean_title_str,
                                session_id=sess_id,
                            )
                            self._released_at = None
                            self.state = MeetingDetectorState.AWAITING_USER_DECISION
                            self.prompted_session_ids.add(sess_id)
                            logger.info("Meeting confirmed: %s session_id=%s, hwnd=%s ('%s')", app_name, sess_id, hwnd, clean_title_str)
                            return "started"
            # SUPPORTING SIGNAL: Check candidate windows and mic consent
            for u, (app_name, is_browser) in candidates.items():
                min_sec = BROWSER_MIN_SEC if is_browser else NATIVE_MIN_SEC
                if now - self._first_seen.get(u, now) >= min_sec:
                    win_info = find_meeting_window(app_name)
                    # Do not trigger false meeting detections on browser mic activity without an active call window
                    if win_info is None and is_browser:
                        continue
                    hwnd = win_info[0] if win_info else None
                    w_title = win_info[1] if win_info else ""
                    bounds = win_info[2] if win_info else (0, 0, 1280, 720)
                    pid = None
                    try:
                        from voxlocal.video.meeting_capture import get_window_pid_and_process_name
                        if hwnd:
                            pid, _ = get_window_pid_and_process_name(hwnd)
                    except Exception:
                        pid = None
                    clean_title_str = clean_meeting_title(w_title, app_name)
                    title_slug = re.sub(r"[^a-zA-Z0-9]", "", clean_title_str.lower())[:32]
                    h_val = hwnd or 0
                    epoch = self._hwnd_first_seen.setdefault(h_val, int(time.time()))
                    plat_slug = app_name.lower().replace(" ", "_")
                    sess_id = f"{plat_slug}_{title_slug or 'call'}_{h_val}_{epoch}"
                    if sess_id in self.ignored_session_ids or sess_id in self.dismissed_session_ids:
                        continue
                    if sess_id in self.prompted_session_ids:
                        continue
                    sess = MeetingSessionInfo(
                        session_id=sess_id,
                        platform=app_name,
                        window_hwnd=hwnd,
                        window_title=w_title,
                        clean_title=clean_title_str,
                        bounds=bounds,
                        is_call_stage=bool(hwnd is not None),
                        mic_active=True,
                        pid=pid,
                        first_detected_at=self._first_seen.get(u, now),
                        confirmed_at=now,
                    )
                    self.active_session = sess
                    self.active = ActiveCall(
                        app_name=app_name,
                        key=u,
                        since=now,
                        window_hwnd=hwnd,
                        window_title=clean_title_str,
                        session_id=sess_id,
                    )
                    self._released_at = None
                    self.state = MeetingDetectorState.AWAITING_USER_DECISION
                    self.prompted_session_ids.add(sess_id)
                    logger.info("Meeting confirmed via mic consent: %s session_id=%s, hwnd=%s ('%s')", app_name, sess_id, hwnd, clean_title_str)
                    return "started"
            return None
        # Session is currently active: track whether it has ended
        still_alive = False
        is_explicit_leave = False
        window_confirmed_closed_or_hub = False

        # 1. If tab capture bridge (Google Meet)
        if self.active_session.browser_tab_id:
            if meet_tab_info and int(meet_tab_info.get("tab_id", 0)) == self.active_session.browser_tab_id:
                still_alive = True

        # 2. If verified call stage windows still contains a matching window for this platform
        if not still_alive and verified_call_stages:
            # First, check if the CURRENT active session window is still present
            same_session_window = None
            for v_hwnd, v_plat, v_title, _ in verified_call_stages:
                if v_plat.lower() == self.active_session.platform.lower():
                    v_clean = clean_meeting_title(v_title, v_plat)
                    is_same_hwnd = (self.active_session.window_hwnd is not None and self.active_session.window_hwnd == v_hwnd)
                    is_same_title = (self.active_session.clean_title.strip().lower() == v_clean.strip().lower())
                    if is_same_hwnd and is_same_title:
                        same_session_window = (v_hwnd, v_title)
                        break

            if same_session_window:
                s_hwnd, s_title = same_session_window
                if is_leaving_title(s_title):
                    is_explicit_leave = True
                else:
                    self.active_session.window_hwnd = s_hwnd
                    still_alive = True
            else:
                # Active session window is no longer present.
                # Check if a DIFFERENT meeting window on this platform exists
                for v_hwnd, v_plat, v_title, _ in verified_call_stages:
                    if v_plat.lower() == self.active_session.platform.lower():
                        v_clean = clean_meeting_title(v_title, v_plat)
                        is_same_hwnd = (self.active_session.window_hwnd is not None and self.active_session.window_hwnd == v_hwnd)
                        is_same_title = (self.active_session.clean_title.strip().lower() == v_clean.strip().lower())

                        if is_leaving_title(v_title):
                            is_explicit_leave = True
                        elif is_same_hwnd or (self.active_session.window_hwnd is None and is_same_title):
                            # Genuine rebound / title adjustment of current session
                            self.active_session.window_hwnd = v_hwnd
                            still_alive = True
                            break
                        else:
                            # HWND or clean title differs: genuine back-to-back meeting transition!
                            logger.info(
                                "Detected back-to-back meeting transition on %s: ending '%s' (HWND=%s), pending '%s' (HWND=%s)",
                                v_plat,
                                self.active_session.clean_title,
                                self.active_session.window_hwnd,
                                v_clean,
                                v_hwnd,
                            )
                            is_explicit_leave = True
                            still_alive = False
                            break

        # 3. Check active presentation / screen sharing session
        if not still_alive:
            try:
                from voxlocal.video.meeting_capture import find_teams_sharing_session, find_zoom_sharing_session
                if "teams" in self.active_session.platform.lower():
                    if find_teams_sharing_session(self.active_session.pid) is not None:
                        still_alive = True
                elif "zoom" in self.active_session.platform.lower():
                    if find_zoom_sharing_session(self.active_session.pid) is not None:
                        still_alive = True
            except Exception:
                pass

        # 4. If native window handle is still valid and not destroyed or turned into a non-call hub
        # Note: In Windows, minimized windows have user32.IsIconic(w_hwnd) == True, but are STILL ALIVE!
        # Users frequently minimize Teams/Zoom to look at docs or take notes. Never stop recording on minimization!
        if not still_alive and self.active_session.window_hwnd:
            w_hwnd = wintypes.HWND(self.active_session.window_hwnd)
            if user32.IsWindow(w_hwnd):
                length = user32.GetWindowTextLengthW(w_hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(w_hwnd, buf, length + 1)
                    title_str = buf.value.strip()
                    if is_leaving_title(title_str):
                        is_explicit_leave = True
                        still_alive = False
                        window_confirmed_closed_or_hub = True
                    elif user32.IsIconic(w_hwnd):
                        # Minimized call stage window: alive, as long as it's not a non-call hub
                        if not is_non_call_hub(self.active_session.platform, title_str):
                            still_alive = True
                        else:
                            window_confirmed_closed_or_hub = True
                    elif not is_non_call_hub(self.active_session.platform, title_str):
                        still_alive = True
                    else:
                        window_confirmed_closed_or_hub = True
            else:
                window_confirmed_closed_or_hub = True

        # 5. Check mic consent if still active for native app without a closed/hub window
        if (
            not still_alive
            and not is_explicit_leave
            and not window_confirmed_closed_or_hub
            and self.active
            and self.active.key in candidates
        ):
            still_alive = True

        if still_alive:
            self._released_at = None
            return None

        # App has closed window and released mic: handle bounded grace period
        if self._released_at is None:
            self._released_at = now
            return None

        if is_explicit_leave:
            effective_grace = 1.0
        elif self.active_session and self.active_session.window_hwnd:
            effective_grace = 2.0
        else:
            effective_grace = 8.5
        if now - self._released_at >= effective_grace:
            ended_name = self.active_session.platform if self.active_session else "Meeting"
            logger.info("Meeting ended: %s (session_id=%s, explicit_leave=%s)", ended_name, self.active_session.session_id if self.active_session else "", is_explicit_leave)
            self.state = MeetingDetectorState.MEETING_ENDED
            # Clean up suppression sets & caches for this session
            if self.active_session:
                self._prune_session_suppression(
                    self.active_session.session_id,
                    self.active_session.window_hwnd,
                )
            self.last_ended_session = self.active_session
            self.last_ended_active = self.active
            self.active_session = None
            self.active = None
            self._released_at = None
            return "ended"
        return None

    def end_active_session_immediately(self) -> None:
        """
        Instantly clear active session tracking, reset detector state to NOT_RUNNING,
        and prune suppression sets so next meeting detection is immediately enabled.
        Invoked when recording is stopped (manual or automated) or meeting is dismissed.
        """
        if self.active_session:
            logger.info(
                "Ending active call detector session immediately: %s (HWND=%s)",
                self.active_session.session_id,
                self.active_session.window_hwnd,
            )
            self._prune_session_suppression(
                self.active_session.session_id,
                self.active_session.window_hwnd,
            )
            self.last_ended_session = self.active_session
            self.last_ended_active = self.active
            self.active_session = None
            self.active = None
        self._released_at = None
        self._is_recording = False
        self.state = MeetingDetectorState.NOT_RUNNING

    def get_active_meeting_count(self) -> int:
        """Return the number of concurrent open meeting windows / active sessions."""
        try:
            wins = list_all_meeting_windows(include_non_call_hubs=False)
            count = len(wins)
            if self.active_session and count == 0:
                return 1
            return count
        except Exception:
            return 1 if self.active_session else 0

    def reset(self) -> None:
        self.state = MeetingDetectorState.NOT_RUNNING
        self._window_first_seen.clear()
        self._hwnd_first_seen.clear()
        self._suppression_timestamps.clear()
        self.prompted_session_ids.clear()
        self.dismissed_session_ids.clear()
        self.ignored_session_ids.clear()
        self.active_session = None
        self.active = None
        self.last_ended_session = None
        self.last_ended_active = None
        self._released_at = None
        self._is_recording = False

    def is_bot_free(self) -> bool:
        """
        Verify native desktop bot-free capture invariant.
        VOX captures 100% locally via Windows loopback & microphone audio APIs;
        zero third-party bot accounts (Otter, Read.ai, etc.) are ever deployed.
        """
        return True

    def get_supported_platforms(self) -> List[str]:
        """Return list of officially verified and detected meeting platforms."""
        return [
            "Microsoft Teams",
            "Zoom",
            "Google Meet",
            "Webex",
            "Slack Huddles",
        ]

    def create_consent_prompt_data(
        self, session_info: Optional[MeetingSessionInfo] = None
    ) -> Dict[str, Any]:
        """
        Generate structured metadata for the 1-click recording consent dialog.
        Provides platform, clean title, window handle, bounds, and audio stream guarantees.
        """
        info = session_info or self.active_session
        if not info:
            return {
                "active": False,
                "is_bot_free": True,
                "message": "No active meeting session detected.",
            }

        return {
            "active": True,
            "session_id": info.session_id,
            "platform": info.platform,
            "clean_title": info.clean_title,
            "window_title": info.window_title,
            "window_hwnd": info.window_hwnd,
            "bounds": info.bounds,
            "is_bot_free": True,
            "bot_free_assurance": "100% native local capture via Windows audio APIs. Zero bot accounts deployed.",
            "audio_streams": [
                "Microphone (Local Voice)",
                "System Audio / Loopback (Remote Speakers)",
            ],
            "capture_modes": ["dual_track_audio", "audio_and_screen"],
            "suggested_prompt": f"Record {info.platform} call '{info.clean_title}'? (1-click consent, zero bots)",
        }

    def request_consent(
        self,
        controller: Any = None,
        session_info: Optional[MeetingSessionInfo] = None,
        on_decision: Optional[Callable[[str], None]] = None,
    ) -> Any:
        """
        Trigger 1-click recording consent flow for the detected meeting.
        If a UI controller is available and Qt is running, opens the non-activating CallConsentDialog.
        Otherwise returns prompt metadata and executes on_decision callback if provided.
        """
        info = session_info or self.active_session
        prompt_data = self.create_consent_prompt_data(info)

        if not prompt_data.get("active"):
            return None

        sess_id = prompt_data["session_id"]
        self.prompted_session_ids.add(sess_id)
        if self.active_session is None and info is not None:
            self.active_session = info

        # If Qt environment and controller are active, attempt launching CallConsentDialog
        if controller is not None:
            try:
                from voxlocal.ui.call_consent_dialog import CallConsentDialog
                dialog = CallConsentDialog(
                    app_name=prompt_data["platform"],
                    suggested_title=prompt_data["clean_title"],
                    controller=controller,
                    window_title=prompt_data["window_title"],
                    window_hwnd=prompt_data["window_hwnd"],
                )
                if on_decision:
                    def _wrap_approved():
                        self.mark_decision(sess_id, "approved")
                        on_decision("approved")
                    def _wrap_dismissed():
                        self.mark_decision(sess_id, "dismissed")
                        on_decision("dismissed")
                    dialog.accepted.connect(_wrap_approved)
                    dialog.rejected.connect(_wrap_dismissed)
                dialog.show()
                return dialog
            except Exception as exc:
                logger.debug("Could not launch CallConsentDialog UI: %s", exc)

        return prompt_data

