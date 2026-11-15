"""
VoxLocal Calendar Base Provider and Dataclass Contracts.
Defines:
- CalendarMeeting: meeting event entity with platform link extraction, attendee normalization, and multi-provider deduplication
- CalendarProviderType: MS_GRAPH, GOOGLE, ZOOM, ICS, LOCAL_MAPI, MOCK
- AccountConnectionState & MeetingPlatform enums
- CalendarAccountInfo: multi-account & multi-tenant state container
- BaseCalendarProvider: abstract interface contract
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class CalendarProviderType(str, Enum):
    MS_GRAPH = "ms_graph"
    GOOGLE = "google"
    ZOOM = "zoom"
    ICS = "ics"
    LOCAL_MAPI = "local_mapi"
    MOCK = "mock"


class AccountConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    SYNCING = "syncing"
    CONNECTED = "connected"
    EXPIRED = "expired"
    ADMIN_APPROVAL_REQUIRED = "admin_approval_required"
    FAILED = "failed"


class MeetingPlatform(str, Enum):
    TEAMS = "teams"
    MEET = "meet"
    ZOOM = "zoom"
    WEBEX = "webex"
    SLACK = "slack"
    IN_PERSON = "in_person"
    GENERIC = "generic"


@dataclass
class CalendarAccountInfo:
    """Persistent state and metadata for one connected calendar account."""
    account_id: str
    user_id: str = "default"
    provider_type: str = CalendarProviderType.MS_GRAPH.value
    account_email: str = ""
    display_name: str = ""
    account_kind: str = "personal"  # "personal" | "work" | "school" | "unknown"
    tenant_id: str = ""
    organization_name: str = ""
    enabled: bool = True
    connection_state: str = AccountConnectionState.DISCONNECTED.value
    last_sync_at: Optional[str] = None
    last_error: Optional[str] = None
    error_code: Optional[str] = None
    selected_calendars: List[str] = field(default_factory=list)
    available_calendars: List[Dict[str, Any]] = field(default_factory=list)
    delta_links: Dict[str, str] = field(default_factory=dict)
    sync_tokens: Dict[str, str] = field(default_factory=dict)


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
    platform: str = MeetingPlatform.GENERIC.value
    is_recurring: bool = False
    provider_type: str = "ms_graph"
    provider_account: str = ""
    source_event_ids: List[str] = field(default_factory=list)
    description: str = ""
    account_id: str = ""
    calendar_id: str = ""
    calendar_name: str = ""
    ical_uid: str = ""
    series_master_id: str = ""
    is_modified_occurrence: bool = False
    timezone_name: str = ""
    source_accounts: List[str] = field(default_factory=list)
    source_providers: List[str] = field(default_factory=list)
    conference_id: str = ""

    def __post_init__(self) -> None:
        if not self.source_event_ids and self.id:
            self.source_event_ids = [self.id]
        if self.account_id and self.account_id not in self.source_accounts:
            self.source_accounts.append(self.account_id)
        elif self.provider_account and self.provider_account not in self.source_accounts:
            self.source_accounts.append(self.provider_account)
        if self.provider_type and self.provider_type not in self.source_providers:
            self.source_providers.append(self.provider_type)
        if self.join_url and not self.is_online:
            self.is_online = True
        if self.join_url and self.platform in ("generic", "teams", "in_person", ""):
            self.platform = self._detect_platform_from_url(self.join_url)
        elif not self.join_url and not self.is_online and self.location and self.platform in ("", "generic"):
            self.platform = MeetingPlatform.IN_PERSON.value

    @staticmethod
    def _detect_platform_from_url(url: str) -> str:
        u = (url or "").lower()
        if "teams.microsoft.com" in u or "teams.live.com" in u or "teams.cloud.microsoft" in u:
            return MeetingPlatform.TEAMS.value
        if "zoom.us" in u or "zoomgov.com" in u:
            return MeetingPlatform.ZOOM.value
        if "meet.google.com" in u:
            return MeetingPlatform.MEET.value
        if "webex.com" in u:
            return MeetingPlatform.WEBEX.value
        if "slack.com" in u:
            return MeetingPlatform.SLACK.value
        return MeetingPlatform.GENERIC.value

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
    def local_start_time(self) -> datetime:
        st = self.start_time
        if st.tzinfo is not None:
            try:
                return st.astimezone()
            except Exception:
                return st
        return st

    @property
    def local_end_time(self) -> datetime:
        et = self.end_time
        if et.tzinfo is not None:
            try:
                return et.astimezone()
            except Exception:
                return et
        return et

    @property
    def duration_minutes(self) -> int:
        try:
            return max(0, int((self.end_time - self.start_time).total_seconds() // 60))
        except Exception:
            return 0

    @property
    def is_today(self) -> bool:
        st_local = self.local_start_time
        now = datetime.now(st_local.tzinfo) if st_local.tzinfo else datetime.now()
        return st_local.date() == now.date()

    @property
    def is_tomorrow(self) -> bool:
        st_local = self.local_start_time
        now = datetime.now(st_local.tzinfo) if st_local.tzinfo else datetime.now()
        return st_local.date() == (now + timedelta(days=1)).date()

    @property
    def is_this_week(self) -> bool:
        st_local = self.local_start_time
        now = datetime.now(st_local.tzinfo) if st_local.tzinfo else datetime.now()
        today = now.date()
        return today <= st_local.date() < (today + timedelta(days=7))

    @property
    def is_happening_now(self) -> bool:
        try:
            ls = self.local_start_time
            le = self.local_end_time
            now = datetime.now(ls.tzinfo) if ls.tzinfo else datetime.now()
            return ls <= now <= le
        except Exception:
            return False

    @property
    def meeting_platform_label(self) -> str:
        labels = {
            MeetingPlatform.TEAMS.value: "Microsoft Teams",
            MeetingPlatform.MEET.value: "Google Meet",
            MeetingPlatform.ZOOM.value: "Zoom",
            MeetingPlatform.WEBEX.value: "Webex",
            MeetingPlatform.SLACK.value: "Slack Huddle",
            MeetingPlatform.IN_PERSON.value: "In Person",
            MeetingPlatform.GENERIC.value: "Online Link" if self.join_url else "Calendar Event",
        }
        return labels.get((self.platform or "").lower(), "Calendar Event")

    @property
    def calendar_provider_label(self) -> str:
        labels = {
            CalendarProviderType.MS_GRAPH.value: "Outlook / M365",
            CalendarProviderType.GOOGLE.value: "Google Calendar",
            CalendarProviderType.ZOOM.value: "Zoom Account",
            CalendarProviderType.ICS.value: "ICS Feed",
            CalendarProviderType.LOCAL_MAPI.value: "Outlook Desktop",
            CalendarProviderType.MOCK.value: "Mock Calendar",
        }
        return labels.get((self.provider_type or "").lower(), self.provider_type or "Calendar")

    @property
    def formatted_time_range(self) -> str:
        try:
            st_local = self.local_start_time
            et_local = self.local_end_time
            st_str = st_local.strftime("%I:%M %p").lstrip("0")
            et_str = et_local.strftime("%I:%M %p").lstrip("0")
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
            return self.local_start_time.strftime("%a, %b %d")
        except Exception:
            return ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "location": self.location,
            "join_url": self.join_url,
            "attendees": list(self.attendees),
            "organizer": self.organizer,
            "is_online": self.is_online,
            "is_cancelled": self.is_cancelled,
            "sensitivity": self.sensitivity,
            "platform": self.platform,
            "is_recurring": self.is_recurring,
            "provider_type": self.provider_type,
            "provider_account": self.provider_account,
            "source_event_ids": list(self.source_event_ids),
            "description": self.description,
            "account_id": self.account_id,
            "calendar_id": self.calendar_id,
            "calendar_name": self.calendar_name,
            "ical_uid": self.ical_uid,
            "series_master_id": self.series_master_id,
            "is_modified_occurrence": self.is_modified_occurrence,
            "timezone_name": self.timezone_name,
            "source_accounts": list(self.source_accounts),
            "source_providers": list(self.source_providers),
            "conference_id": self.conference_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CalendarMeeting":
        st_raw = data.get("start_time")
        et_raw = data.get("end_time")
        st = st_raw if isinstance(st_raw, datetime) else datetime.fromisoformat(str(st_raw).replace("Z", "+00:00"))
        et = et_raw if isinstance(et_raw, datetime) else datetime.fromisoformat(str(et_raw).replace("Z", "+00:00"))
        return cls(
            id=str(data.get("id") or data.get("external_id") or ""),
            title=str(data.get("title") or "Untitled Meeting"),
            start_time=st,
            end_time=et,
            location=str(data.get("location") or ""),
            join_url=data.get("join_url") or None,
            attendees=list(data.get("attendees") or []),
            organizer=str(data.get("organizer") or ""),
            is_online=bool(data.get("is_online", False)),
            is_cancelled=bool(data.get("is_cancelled", False)),
            sensitivity=str(data.get("sensitivity") or "normal"),
            platform=str(data.get("platform") or MeetingPlatform.GENERIC.value),
            is_recurring=bool(data.get("is_recurring", False)),
            provider_type=str(data.get("provider_type") or CalendarProviderType.MS_GRAPH.value),
            provider_account=str(data.get("provider_account") or ""),
            source_event_ids=list(data.get("source_event_ids") or []),
            description=str(data.get("description") or ""),
            account_id=str(data.get("account_id") or ""),
            calendar_id=str(data.get("calendar_id") or ""),
            calendar_name=str(data.get("calendar_name") or ""),
            ical_uid=str(data.get("ical_uid") or ""),
            series_master_id=str(data.get("series_master_id") or ""),
            is_modified_occurrence=bool(data.get("is_modified_occurrence", False)),
            timezone_name=str(data.get("timezone_name") or ""),
            source_accounts=list(data.get("source_accounts") or []),
            source_providers=list(data.get("source_providers") or []),
            conference_id=str(data.get("conference_id") or ""),
        )


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

    def list_calendars(self) -> List[Dict[str, Any]]:
        """List available calendars for the authenticated account."""
        return []

    def set_selected_calendars(self, calendar_ids: List[str]) -> None:
        """Select subset of calendars to synchronize."""
        return None

    def get_selected_calendars(self) -> List[str]:
        """Return selected calendar IDs (empty means primary/default)."""
        return []

    def get_account_info(self) -> Dict[str, Any]:
        """Return normalized metadata for this provider account."""
        return dict(self.get_provider_status())

    @property
    def provider_type(self) -> str:
        """Canonical provider identifier (e.g. ms_graph, google, zoom, ics, local_mapi)."""
        status_attr = getattr(self, "_status", None)
        if isinstance(status_attr, dict) and status_attr.get("provider_type"):
            return str(status_attr["provider_type"])
        try:
            st = self.get_provider_status() or {}
            if st.get("provider_type"):
                return str(st["provider_type"])
        except Exception:
            pass
        return "unknown"
