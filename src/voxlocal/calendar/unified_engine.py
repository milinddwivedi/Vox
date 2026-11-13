"""
VoxLocal Unified Calendar Engine & Multi-Provider Aggregator.
Implements Feature Contract:
- Common calendar integration layer aggregating Microsoft 365, Google Calendar, Zoom, and ICS
- High-precision cross-calendar meeting deduplication by meeting ID, video join URL, and fuzzy time/title
- Horizon categorization (Today, Tomorrow, Next 30 Days) with time zone normalization
- Incremental background synchronization and resilient offline/expired-token recovery
- Qt Signals for UI lists, 1-click meeting join, and Notetaker integration
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from voxlocal.calendar.base import BaseCalendarProvider, CalendarMeeting, CalendarProviderType
from voxlocal.calendar.calendar_manager import CalendarAlertEvaluator
from voxlocal.core.logger import get_logger

logger = get_logger("calendar.unified_engine")


def normalize_meeting_join_id(url: Optional[str]) -> str:
    """
    Extract a normalized meeting identifier from Zoom, Teams, Google Meet, or Webex URLs.
    Allows exact cross-provider matching when an event is mirrored across calendars.
    """
    if not url:
        return ""
    u = url.lower().strip()

    # 1. Zoom: extract numeric meeting ID
    m_zoom = re.search(r"zoom(?:\.us|gov\.com)/j/([0-9]{9,12})", u)
    if m_zoom:
        return f"zoom_{m_zoom.group(1)}"

    # 2. Google Meet: extract 3-4-3 code
    m_meet = re.search(r"meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})", u)
    if m_meet:
        return f"meet_{m_meet.group(1)}"

    # 3. Microsoft Teams: extract thread ID or message ID
    m_teams = re.search(r"19%3ameeting_([a-zA-Z0-9_\-]+)%40thread", u)
    if m_teams:
        return f"teams_{m_teams.group(1)}"
    m_teams_alt = re.search(r"/meetup-join/([^/?#]+)", u)
    if m_teams_alt:
        return f"teams_{m_teams_alt.group(1)[:40]}"

    # 4. Webex: extract meet ID
    m_webex = re.search(r"webex\.com/(?:meet|join)/([a-zA-Z0-9_\-\.]+)", u)
    if m_webex:
        return f"webex_{m_webex.group(1)}"

    # Strip query params and protocol
    cleaned = re.sub(r"^https?://", "", u).split("?")[0].rstrip("/")
    return cleaned[:80] if len(cleaned) > 10 else ""


def normalize_title(title: str) -> str:
    """Normalize title for fuzzy deduplication."""
    clean = re.sub(r"[^\w\s]", "", (title or "").lower())
    words = [w for w in clean.split() if w not in ("meeting", "sync", "call", "catchup", "discussion", "re")]
    return " ".join(words).strip()


class UnifiedCalendarEngine(QObject):
    """
    Central Multi-Provider Calendar Engine for VOX Notetaker.
    Manages Microsoft 365, Google Calendar, Zoom, and ICS providers.
    """

    upcoming_meetings_updated = pyqtSignal(list)       # List[CalendarMeeting]
    providers_status_changed = pyqtSignal(dict)        # Dict[str, Dict[str, Any]]
    meeting_alert = pyqtSignal(object, int, str)       # (CalendarMeeting, minutes_until_start, alert_type)
    acoustic_priming_ready = pyqtSignal(list)          # List[str] keywords
    start_capture_requested = pyqtSignal(object)       # CalendarMeeting
    sync_started = pyqtSignal()
    sync_finished = pyqtSignal(int)                    # count of synced meetings

    def __init__(
        self,
        providers: Optional[Dict[str, BaseCalendarProvider]] = None,
        parent: Optional[QObject] = None,
        poll_interval_sec: int = 60,
    ) -> None:
        super().__init__(parent)
        self._providers: Dict[str, BaseCalendarProvider] = dict(providers or {})
        self._evaluator = CalendarAlertEvaluator()
        self._cached_meetings: List[CalendarMeeting] = []
        self._lock = threading.Lock()
        self._is_syncing: bool = False

        self._timer = QTimer(self)
        self._timer.setInterval(max(10, poll_interval_sec) * 1000)
        self._timer.timeout.connect(self.poll_all_now)

    @property
    def providers(self) -> Dict[str, BaseCalendarProvider]:
        with self._lock:
            return dict(self._providers)

    def register_provider(self, key: str, provider: BaseCalendarProvider) -> None:
        """Register or update an active calendar provider."""
        with self._lock:
            self._providers[key] = provider
        logger.info("Registered calendar provider '%s'", key)
        self.emit_providers_status()
        self.poll_all_now()

    def unregister_provider(self, key: str) -> bool:
        """Remove and disconnect a calendar provider."""
        with self._lock:
            prov = self._providers.pop(key, None)
        if prov:
            prov.disconnect()
            logger.info("Unregistered calendar provider '%s'", key)
            self.emit_providers_status()
            self.poll_all_now()
            return True
        return False

    def get_provider(self, key: str) -> Optional[BaseCalendarProvider]:
        with self._lock:
            return self._providers.get(key)

    def start_polling(self, interval_sec: Optional[int] = None) -> None:
        """Start background synchronisation daemon."""
        if interval_sec:
            self._timer.setInterval(max(10, interval_sec) * 1000)
        self._timer.start()
        logger.info("UnifiedCalendarEngine polling started (%ds interval)", self._timer.interval() // 1000)
        self.poll_all_now()

    def stop_polling(self) -> None:
        """Stop background synchronisation daemon."""
        self._timer.stop()
        logger.info("UnifiedCalendarEngine polling stopped")

    def emit_providers_status(self) -> None:
        """Emit composite status dictionary for all registered providers."""
        status_map: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            for k, prov in self._providers.items():
                try:
                    status_map[k] = prov.get_provider_status()
                except Exception as exc:
                    status_map[k] = {"provider_type": k, "is_healthy": False, "error_state": str(exc)}
        self.providers_status_changed.emit(status_map)

    def poll_all_now(self) -> List[CalendarMeeting]:
        """
        Execute an immediate parallel synchronisation across all connected providers.
        Thread-safe: spawns worker thread if called from UI.
        """
        if self._is_syncing:
            return self.get_upcoming_meetings()

        self._is_syncing = True
        self.sync_started.emit()

        def _worker() -> None:
            raw_meetings: List[CalendarMeeting] = []
            providers_snapshot = self.providers

            for key, prov in providers_snapshot.items():
                if not prov.is_authenticated():
                    continue
                try:
                    # Fetch 30-day forward window (43,200 minutes)
                    items = prov.get_upcoming_meetings(window_minutes=43200)
                    for it in items:
                        if not it.provider_type:
                            it.provider_type = key
                    raw_meetings.extend(items)
                except Exception as exc:
                    logger.error("Error polling calendar provider '%s': %s", key, exc)

            # High-precision deduplication
            deduped = self._deduplicate_meetings(raw_meetings)

            # Sort chronologically by start_time
            def _get_sort_key(m: CalendarMeeting) -> float:
                st = m.start_time
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
                return st.timestamp()

            deduped.sort(key=_get_sort_key)

            with self._lock:
                self._cached_meetings = deduped
                self._is_syncing = False

            # Evaluate intelligent alerts
            alerts = self._evaluator.evaluate_meeting_alerts(deduped)
            for alert in alerts:
                m = alert["meeting"]
                mins = alert["minutes_until_start"]
                a_type = alert["alert_type"]

                if a_type == "15min":
                    priming_terms = self._evaluator.extract_acoustic_priming_terms(m)
                    self.acoustic_priming_ready.emit(priming_terms)

                self.meeting_alert.emit(m, mins, a_type)

            self.upcoming_meetings_updated.emit(deduped)
            self.emit_providers_status()
            self.sync_finished.emit(len(deduped))

        threading.Thread(target=_worker, daemon=True, name="vox-calendar-sync").start()
        return self.get_upcoming_meetings()

    def _deduplicate_meetings(self, meetings: List[CalendarMeeting]) -> List[CalendarMeeting]:
        """
        Deduplicate meetings appearing in multiple connected calendars.
        Matches by:
        1. Exact matching normalized meeting join URL / ID
        2. Same start time (+/- 5 min) and matching normalized title
        Merges attendees, keeps best video link and platform, and tags sources.
        """
        merged_list: List[CalendarMeeting] = []

        for item in meetings:
            if item.is_cancelled:
                continue

            join_id = normalize_meeting_join_id(item.join_url)
            norm_t = normalize_title(item.title)
            st_ts = item.start_time.timestamp()

            matched_idx: Optional[int] = None
            for idx, existing in enumerate(merged_list):
                # Check 1: Join ID match
                ex_join_id = normalize_meeting_join_id(existing.join_url)
                if join_id and ex_join_id and join_id == ex_join_id:
                    matched_idx = idx
                    break

                # Check 2: Time window (+/- 5 min) and Title match
                ex_ts = existing.start_time.timestamp()
                if abs(st_ts - ex_ts) <= 300:
                    ex_norm_t = normalize_title(existing.title)
                    if norm_t and ex_norm_t and (norm_t == ex_norm_t or norm_t in ex_norm_t or ex_norm_t in norm_t):
                        matched_idx = idx
                        break

            if matched_idx is not None:
                # Merge into existing item
                target = merged_list[matched_idx]
                # Merge attendee lists
                combined_attendees = list(target.attendees)
                for att in item.attendees:
                    if att and att not in combined_attendees:
                        combined_attendees.append(att)
                target.attendees = combined_attendees

                # Prefer richer join URL & video platform
                if not target.join_url and item.join_url:
                    target.join_url = item.join_url
                    target.platform = item.platform
                    target.is_online = True
                elif target.platform == "generic" and item.platform != "generic":
                    target.platform = item.platform

                # Record source event IDs
                if item.id and item.id not in target.source_event_ids:
                    target.source_event_ids.append(item.id)

                # Composite provider attribution
                if item.provider_type and item.provider_type not in target.provider_type:
                    target.provider_type = f"{target.provider_type}+{item.provider_type}"
            else:
                merged_list.append(item)

        return merged_list

    def get_upcoming_meetings(self) -> List[CalendarMeeting]:
        """Return cached synchronized meetings."""
        with self._lock:
            return list(self._cached_meetings)

    def get_today_meetings(self) -> List[CalendarMeeting]:
        """Return meetings scheduled for today."""
        return [m for m in self.get_upcoming_meetings() if m.is_today]

    def get_tomorrow_meetings(self) -> List[CalendarMeeting]:
        """Return meetings scheduled for tomorrow."""
        return [m for m in self.get_upcoming_meetings() if m.is_tomorrow]

    def get_future_meetings(self, days: int = 30) -> List[CalendarMeeting]:
        """Return future meetings beyond tomorrow up to horizon."""
        now_utc = datetime.now(timezone.utc)
        horizon = now_utc + timedelta(days=days)
        result = []
        for m in self.get_upcoming_meetings():
            if m.is_today or m.is_tomorrow:
                continue
            st = m.start_time
            if st.tzinfo is None:
                st = st.replace(tzinfo=timezone.utc)
            if st <= horizon:
                result.append(m)
        return result

    def trigger_capture(self, meeting: CalendarMeeting) -> None:
        """User clicked 1-click capture for meeting."""
        logger.info("1-Click meeting capture triggered: '%s' (%s)", meeting.title, meeting.platform)
        self.start_capture_requested.emit(meeting)

    def poll_now(self) -> List[CalendarMeeting]:
        """Compatibility alias for poll_all_now."""
        return self.poll_all_now()

    def set_provider(self, provider: BaseCalendarProvider, key: Optional[str] = None) -> None:
        """Compatibility adapter for legacy set_provider callers."""
        k = key or getattr(provider, "provider_type", None) or "default"
        if hasattr(k, "value"):
            k = k.value
        self.register_provider(str(k), provider)

    def disconnect_provider(self, key: Optional[str] = None) -> bool:
        """Disconnect one or all providers."""
        if key:
            return self.unregister_provider(key)
        with self._lock:
            keys = list(self._providers.keys())
        for k in keys:
            self.unregister_provider(k)
        self.clear_cache()
        return True

    def clear_cache(self) -> None:
        """Clear cached events."""
        with self._lock:
            self._cached_meetings.clear()
        self.upcoming_meetings_updated.emit([])
