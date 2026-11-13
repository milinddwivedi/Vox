"""
VoxLocal Calendar Base Provider and Dataclass Contracts.
Defines:
- CalendarMeeting: meeting event entity with platform link extraction, attendee normalization, and multi-provider deduplication
- CalendarProviderType: MS_GRAPH, GOOGLE, ZOOM, ICS, LOCAL_MAPI, MOCK
- BaseCalendarProvider: abstract interface contract
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class CalendarProviderType(str, Enum):
    MS_GRAPH = "ms_graph"
    GOOGLE = "google"
    ZOOM = "zoom"
    ICS = "ics"
    LOCAL_MAPI = "local_mapi"
    MOCK = "mock"


@dataclass
class CalendarMeeting:
    """Unified Calendar Meeting entity across Microsoft 365, Google Calendar, Zoom, and ICS."""
    id: str
    title: str
    start_time: datetime
    end_time: datetime
    location: str = ""
    join_url: Optional[str] = None
    attendees: List[str] = field(default_factory=list)
    organizer: str = ""
    is_online: bool = False
    is_cancelled: bool = False
    sensitivity: str = "normal"
    platform: str = "teams"
    is_recurring: bool = False
    provider_type: str = "ms_graph"
    provider_account: str = ""
    source_event_ids: List[str] = field(default_factory=list)
    description: str = ""

    def __post_init__(self) -> None:
        if not self.source_event_ids and self.id:
            self.source_event_ids = [self.id]
        if self.join_url and not self.is_online:
            self.is_online = True
        if self.join_url and self.platform in ("generic", "teams", ""):
            self.platform = self._detect_platform_from_url(self.join_url)

    @staticmethod
    def _detect_platform_from_url(url: str) -> str:
        u = (url or "").lower()
        if "teams.microsoft.com" in u or "teams.live.com" in u:
            return "teams"
        if "zoom.us" in u or "zoomgov.com" in u:
            return "zoom"
        if "meet.google.com" in u:
            return "meet"
        if "webex.com" in u:
            return "webex"
        if "slack.com" in u:
            return "slack"
        return "generic"

    @property
    def subject(self) -> str:
        return self.title

    @property
    def event_id(self) -> str:
        return self.id

    @property
    def organizer_name(self) -> str:
        return self.organizer

    @property
    def attendees_str(self) -> str:
        return ", ".join(self.attendees) if self.attendees else ""

    @property
    def duration_minutes(self) -> int:
        try:
            return max(0, int((self.end_time - self.start_time).total_seconds() // 60))
        except Exception:
            return 0

    @property
    def is_today(self) -> bool:
        now = datetime.now()
        st = self.start_time
        if st.tzinfo:
            now = datetime.now(st.tzinfo)
        return st.date() == now.date()

    @property
    def is_tomorrow(self) -> bool:
        now = datetime.now()
        st = self.start_time
        if st.tzinfo:
            now = datetime.now(st.tzinfo)
        from datetime import timedelta
        return st.date() == (now + timedelta(days=1)).date()

    @property
    def formatted_time_range(self) -> str:
        try:
            st_str = self.start_time.strftime("%I:%M %p").lstrip("0")
            et_str = self.end_time.strftime("%I:%M %p").lstrip("0")
            dur = self.duration_minutes
            dur_str = f"{dur}m" if dur < 60 else f"{dur // 60}h {dur % 60}m" if dur % 60 else f"{dur // 60}h"
            return f"{st_str} - {et_str} ({dur_str})"
        except Exception:
            return ""

    @property
    def formatted_date(self) -> str:
        try:
            if self.is_today:
                return "Today"
            if self.is_tomorrow:
                return "Tomorrow"
            return self.start_time.strftime("%a, %b %d")
        except Exception:
            return ""


class BaseCalendarProvider(ABC):
    """Abstract calendar provider interface contract."""

    @abstractmethod
    def authenticate(self, interactive: bool = True) -> bool:
        """Authenticate or verify valid session token."""
        pass

    @abstractmethod
    def disconnect(self) -> bool:
        """Revoke tokens, clear cache, and transition to unauthenticated."""
        pass

    @abstractmethod
    def is_authenticated(self) -> bool:
        """Return True if session is authenticated and active."""
        pass

    @abstractmethod
    def get_upcoming_meetings(self, window_minutes: int = 60) -> List[CalendarMeeting]:
        """Fetch scheduled meetings within forward window."""
        pass

    @abstractmethod
    def get_provider_status(self) -> Dict[str, Any]:
        """Return provider type, health, last poll timestamp, and error state."""
        pass

    def get_upcoming_events(self, window_minutes: int = 60) -> List[CalendarMeeting]:
        """Alias for get_upcoming_meetings."""
        return self.get_upcoming_meetings(window_minutes=window_minutes)
