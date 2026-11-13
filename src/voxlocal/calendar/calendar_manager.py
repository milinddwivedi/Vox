"""
VoxLocal Calendar Manager & Alert Engine.
Coordinates:
- Background polling daemon (default 60s cadence) across active BaseCalendarProvider
- Upcoming meeting detection with 15-minute and 5-minute advance thresholds
- SQLite & in-memory state deduplication to prevent repetitive alert prompts
- Participant name and subject keyword extraction for speech model acoustic priming
- Qt Signals for UI toasts, notifications, and 1-click meeting capture
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from voxlocal.calendar.base import BaseCalendarProvider, CalendarMeeting
from voxlocal.calendar.mock_provider import MockCalendarProvider
from voxlocal.core.logger import get_logger

logger = get_logger("calendar.manager")


class CalendarAlertEvaluator:
    """Evaluates upcoming meetings for 15-minute and 5-minute advance notification triggers."""

    def __init__(self) -> None:
        self._alerted_cache: Set[str] = set()

    def evaluate_meeting_alerts(
        self,
        meetings: List[CalendarMeeting],
        current_time: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Evaluate meetings against advance notification thresholds:
        - 15-Minute Advance: 5.0 < minutes <= 15.0 (Acoustic Priming)
        - 5-Minute Imminent: 0.0 < minutes <= 5.0 (Toast / Floating Banner)
        """
        now = current_time or datetime.now(timezone.utc)
        triggered_alerts: List[Dict[str, Any]] = []

        for m in meetings:
            if m.is_cancelled:
                continue

            # Ensure start_time has timezone
            st = m.start_time
            if st.tzinfo is None:
                st = st.replace(tzinfo=timezone.utc)

            minutes_until_start = (st - now).total_seconds() / 60.0
            start_epoch = int(st.timestamp())

            # 15-Minute Advance Alert: 5 < minutes <= 15
            if 5.0 < minutes_until_start <= 15.0:
                dedup_key = f"alert_15m_{m.id}_{start_epoch}"
                if dedup_key not in self._alerted_cache:
                    self._alerted_cache.add(dedup_key)
                    triggered_alerts.append({
                        "event_id": m.id,
                        "alert_type": "15min",
                        "minutes_until_start": int(minutes_until_start),
                        "meeting": m,
                        "action": "acoustic_priming",
                    })

            # 5-Minute Imminent Alert: 0 < minutes <= 5
            elif 0.0 < minutes_until_start <= 5.0:
                dedup_key = f"alert_5m_{m.id}_{start_epoch}"
                if dedup_key not in self._alerted_cache:
                    self._alerted_cache.add(dedup_key)
                    triggered_alerts.append({
                        "event_id": m.id,
                        "alert_type": "5min",
                        "minutes_until_start": int(minutes_until_start),
                        "meeting": m,
                        "action": "show_toast_prompt",
                    })

        return triggered_alerts

    def extract_acoustic_priming_terms(self, meeting: CalendarMeeting) -> List[str]:
        """
        Extract attendee names and domain acronyms/keywords from meeting
        to inject into speech recognizer custom vocabulary.
        """
        terms: List[str] = []
        # Attendee names
        for att in meeting.attendees:
            clean = att.strip()
            if clean and clean not in terms:
                terms.append(clean)

        if meeting.organizer and meeting.organizer.strip() not in terms:
            terms.append(meeting.organizer.strip())

        # Extract uppercase acronyms and proper nouns from title
        title_tokens = re.findall(r"\b[A-Z][a-zA-Z0-9_\-\.]{2,}\b", meeting.title)
        for tok in title_tokens:
            if tok not in terms:
                terms.append(tok)

        return terms


class CalendarManager(QObject):
    """
    Central Calendar Service running background polling, alert triggers,
    and acoustic vocabulary priming for VOX.
    """

    # Signals
    meeting_alert = pyqtSignal(object, int, str)  # (CalendarMeeting, minutes_until_start, alert_type)
    upcoming_meetings_updated = pyqtSignal(list)  # (List[CalendarMeeting])
    provider_status_changed = pyqtSignal(dict)  # (Dict[str, Any])
    acoustic_priming_ready = pyqtSignal(list)  # (List[str])
    start_capture_requested = pyqtSignal(object)  # (CalendarMeeting)

    def __init__(
        self,
        provider: Optional[BaseCalendarProvider] = None,
        parent: Optional[QObject] = None,
        poll_interval_sec: int = 60,
    ) -> None:
        super().__init__(parent)
        self._provider: BaseCalendarProvider = provider or MockCalendarProvider()
        self._evaluator = CalendarAlertEvaluator()
        self._cached_upcoming: List[CalendarMeeting] = []

        # Polling daemon timer
        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_sec * 1000)
        self._timer.timeout.connect(self.poll_now)

    @property
    def provider(self) -> BaseCalendarProvider:
        return self._provider

    def set_provider(self, provider: BaseCalendarProvider) -> None:
        """Switch active calendar provider."""
        self._provider = provider
        self.provider_status_changed.emit(self._provider.get_provider_status())
        self.poll_now()

    def start_polling(self, interval_sec: Optional[int] = None) -> None:
        """Start the background calendar polling daemon."""
        if interval_sec:
            self._timer.setInterval(interval_sec * 1000)
        self._timer.start()
        logger.info("CalendarManager polling started (%ds interval)", self._timer.interval() // 1000)
        self.poll_now()

    def stop_polling(self) -> None:
        """Stop background polling timer."""
        self._timer.stop()
        logger.info("CalendarManager polling stopped")

    def poll_now(self) -> List[CalendarMeeting]:
        """Execute an immediate poll of upcoming calendar events."""
        if not self._provider.is_authenticated():
            status = self._provider.get_provider_status()
            self.provider_status_changed.emit(status)
            return []

        try:
            meetings = self._provider.get_upcoming_meetings(window_minutes=60)
            self._cached_upcoming = list(meetings)
            self.upcoming_meetings_updated.emit(meetings)

            alerts = self._evaluator.evaluate_meeting_alerts(meetings)
            for alert in alerts:
                m = alert["meeting"]
                mins = alert["minutes_until_start"]
                a_type = alert["alert_type"]

                if a_type == "15min":
                    priming_terms = self._evaluator.extract_acoustic_priming_terms(m)
                    self.acoustic_priming_ready.emit(priming_terms)

                self.meeting_alert.emit(m, mins, a_type)

            status = self._provider.get_provider_status()
            self.provider_status_changed.emit(status)
            return meetings
        except Exception as exc:
            logger.error("Error during calendar polling: %s", exc)
            return []

    def get_upcoming_meetings(self) -> List[CalendarMeeting]:
        """Return the most recently polled upcoming meetings."""
        return list(self._cached_upcoming)

    def trigger_capture(self, meeting: CalendarMeeting) -> None:
        """User clicked 1-click capture on meeting alert prompt."""
        logger.info("1-Click meeting capture triggered for: %s", meeting.title)
        self.start_capture_requested.emit(meeting)

    def disconnect_provider(self) -> bool:
        """Disconnect active provider and clear cache."""
        res = self._provider.disconnect()
        self._cached_upcoming.clear()
        self.upcoming_meetings_updated.emit([])
        self.provider_status_changed.emit(self._provider.get_provider_status())
        return res
