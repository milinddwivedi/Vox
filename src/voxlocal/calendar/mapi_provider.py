"""
VoxLocal Local Windows Outlook MAPI Calendar Provider.
Zero-cloud-egress corporate desktop fallback connecting directly to
local Outlook desktop via Win32 COM Automation (win32com.client).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from voxlocal.calendar.base import (
    AccountConnectionState,
    BaseCalendarProvider,
    CalendarMeeting,
    CalendarProviderType,
)
from voxlocal.calendar.graph_provider import extract_meeting_links
from voxlocal.core.logger import get_logger

logger = get_logger("calendar.mapi")


class LocalMapiCalendarProvider(BaseCalendarProvider):
    """
    Local Outlook MAPI Provider using Win32 COM automation.
    Requires local Outlook desktop to be installed and configured.
    """

    def __init__(self, account_id: str = "local_mapi:default", user_id: str = "default") -> None:
        self.user_id = user_id or "default"
        self._account_id = account_id or "local_mapi:default"
        self._authenticated: bool = False
        self._outlook_app: Any = None
        self._calendar_folder: Any = None
        self._status: Dict[str, Any] = {
            "provider_type": CalendarProviderType.LOCAL_MAPI.value,
            "account_id": self._account_id,
            "account_upn": "Outlook Desktop (MAPI)",
            "display_name": "Outlook Desktop (MAPI)",
            "account_kind": "work",
            "connection_state": AccountConnectionState.DISCONNECTED.value,
            "is_healthy": True,
            "last_poll_time": None,
            "last_sync_at": None,
            "error_state": None,
        }

    def authenticate(self, interactive: bool = True) -> bool:
        """Connect to local Outlook COM application and MAPI calendar folder."""
        try:
            import win32com.client  # type: ignore

            self._outlook_app = win32com.client.Dispatch("Outlook.Application")
            mapi = self._outlook_app.GetNamespace("MAPI")
            self._calendar_folder = mapi.GetDefaultFolder(9)
            self._authenticated = True
            self._status["is_healthy"] = True
            self._status["error_state"] = None
            self._status["connection_state"] = AccountConnectionState.CONNECTED.value
            logger.info("Successfully connected to local Outlook MAPI calendar")
            return True
        except ImportError:
            logger.warning("pywin32 not installed; Outlook MAPI COM unavailable")
            self._authenticated = False
            self._status["is_healthy"] = False
            self._status["error_state"] = "PYWIN32_UNAVAILABLE"
            self._status["connection_state"] = AccountConnectionState.FAILED.value
            return False
        except Exception as exc:
            logger.warning("Failed connecting to local Outlook MAPI COM: %s", exc)
            self._authenticated = False
            self._status["is_healthy"] = False
            self._status["error_state"] = "OUTLOOK_COM_UNAVAILABLE"
            self._status["connection_state"] = AccountConnectionState.FAILED.value
            return False

    def disconnect(self) -> bool:
        """Release COM references and mark unauthenticated."""
        self._calendar_folder = None
        self._outlook_app = None
        self._authenticated = False
        self._status["is_healthy"] = True
        self._status["error_state"] = "DISCONNECTED"
        self._status["connection_state"] = AccountConnectionState.DISCONNECTED.value
        return True

    def is_authenticated(self) -> bool:
        """Return True if COM handle is connected and active."""
        return self._authenticated and self._calendar_folder is not None

    def get_upcoming_meetings(self, window_minutes: int = 60) -> List[CalendarMeeting]:
        """Query scheduled events in local Outlook desktop within window_minutes."""
        now_iso = datetime.now(timezone.utc).isoformat()
        self._status["last_poll_time"] = now_iso
        if not self.is_authenticated():
            if not self.authenticate(interactive=False):
                return []

        meetings: List[CalendarMeeting] = []
        try:
            items = self._calendar_folder.Items
            items.IncludeRecurrences = True
            items.Sort("[Start]")

            now_dt = datetime.now(timezone.utc)
            end_dt = now_dt + timedelta(minutes=window_minutes)
            now_str = now_dt.strftime("%m/%d/%Y %H:%M")
            end_str = end_dt.strftime("%m/%d/%Y %H:%M")

            filter_str = f"[Start] >= '{now_str}' AND [Start] <= '{end_str}'"
            restricted_items = items.Restrict(filter_str)

            for item in restricted_items:
                try:
                    subject = str(getattr(item, "Subject", "Meeting") or "Meeting")
                    start = getattr(item, "Start", None)
                    end = getattr(item, "End", None)
                    location = str(getattr(item, "Location", "") or "")
                    body = str(getattr(item, "Body", "") or "")
                    organizer = str(getattr(item, "Organizer", "") or "")
                    req_attendees = str(getattr(item, "RequiredAttendees", "") or "")
                    entry_id = str(getattr(item, "EntryID", "") or f"mapi-{hash(subject)}")
                    is_recurring = bool(getattr(item, "IsRecurring", False))

                    st_dt = start if isinstance(start, datetime) else now_dt
                    et_dt = end if isinstance(end, datetime) else (st_dt + timedelta(minutes=30))
                    if st_dt.tzinfo is None:
                        st_dt = st_dt.replace(tzinfo=timezone.utc)
                    if et_dt.tzinfo is None:
                        et_dt = et_dt.replace(tzinfo=timezone.utc)

                    attendees_list = [a.strip() for a in req_attendees.split(";") if a.strip()]
                    join_url, platform = extract_meeting_links(f"{location}\n{body}")

                    meetings.append(
                        CalendarMeeting(
                            id=entry_id,
                            title=subject,
                            start_time=st_dt,
                            end_time=et_dt,
                            location=location,
                            join_url=join_url,
                            attendees=attendees_list,
                            organizer=organizer,
                            is_online=bool(join_url or "teams" in location.lower() or "zoom" in location.lower()),
                            platform=platform,
                            is_recurring=is_recurring,
                            provider_type=CalendarProviderType.LOCAL_MAPI.value,
                            provider_account="Outlook Desktop (MAPI)",
                            description=body[:500].strip(),
                            account_id=self._account_id,
                        )
                    )
                except Exception as item_exc:
                    logger.debug("Failed parsing MAPI calendar item: %s", item_exc)
                    continue

            self._status["is_healthy"] = True
            self._status["error_state"] = None
            self._status["connection_state"] = AccountConnectionState.CONNECTED.value
            self._status["last_sync_at"] = now_iso
            return meetings

        except Exception as exc:
            logger.error("Failed querying Outlook MAPI items: %s", exc)
            self._status["is_healthy"] = False
            self._status["error_state"] = "MAPI_QUERY_ERROR"
            self._status["connection_state"] = AccountConnectionState.FAILED.value
            return []

    def get_provider_status(self) -> Dict[str, Any]:
        """Return provider health status dictionary."""
        return dict(self._status)
