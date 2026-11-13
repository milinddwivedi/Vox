"""
VoxLocal Local Windows Outlook MAPI Calendar Provider.
Zero-cloud-egress corporate desktop fallback connecting directly to
local Outlook desktop via Win32 COM Automation (win32com.client).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from voxlocal.calendar.base import BaseCalendarProvider, CalendarMeeting, CalendarProviderType
from voxlocal.core.logger import get_logger

logger = get_logger("calendar.mapi")


class LocalMapiCalendarProvider(BaseCalendarProvider):
    """
    Local Outlook MAPI Provider using Win32 COM automation.
    Requires local Outlook desktop to be installed and configured.
    """

    def __init__(self) -> None:
        self._authenticated: bool = False
        self._outlook_app: Any = None
        self._calendar_folder: Any = None
        self._status: Dict[str, Any] = {
            "provider_type": CalendarProviderType.LOCAL_MAPI.value,
            "is_healthy": True,
            "last_poll_time": None,
            "error_state": None,
        }

    def authenticate(self, interactive: bool = True) -> bool:
        """Connect to local Outlook COM application and MAPI calendar folder."""
        try:
            import win32com.client  # type: ignore

            self._outlook_app = win32com.client.Dispatch("Outlook.Application")
            mapi = self._outlook_app.GetNamespace("MAPI")
            # 9 = olFolderCalendar
            self._calendar_folder = mapi.GetDefaultFolder(9)
            self._authenticated = True
            self._status["is_healthy"] = True
            self._status["error_state"] = None
            logger.info("Successfully connected to local Outlook MAPI calendar")
            return True
        except ImportError:
            logger.warning("pywin32 not installed; Outlook MAPI COM unavailable")
            self._authenticated = False
            self._status["is_healthy"] = False
            self._status["error_state"] = "PYWIN32_UNAVAILABLE"
            return False
        except Exception as exc:
            logger.warning("Failed connecting to local Outlook MAPI COM: %s", exc)
            self._authenticated = False
            self._status["is_healthy"] = False
            self._status["error_state"] = "OUTLOOK_COM_UNAVAILABLE"
            return False

    def disconnect(self) -> bool:
        """Release COM references and mark unauthenticated."""
        self._calendar_folder = None
        self._outlook_app = None
        self._authenticated = False
        self._status["is_healthy"] = True
        self._status["error_state"] = "DISCONNECTED"
        return True

    def is_authenticated(self) -> bool:
        """Return True if COM handle is connected and active."""
        return self._authenticated and self._calendar_folder is not None

    def get_upcoming_meetings(self, window_minutes: int = 60) -> List[CalendarMeeting]:
        """Query scheduled events in local Outlook desktop within window_minutes."""
        self._status["last_poll_time"] = datetime.now(timezone.utc).isoformat()
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

            # Jet query filter
            filter_str = f"[Start] >= '{now_str}' AND [Start] <= '{end_str}'"
            restricted_items = items.Restrict(filter_str)

            for item in restricted_items:
                try:
                    subject = getattr(item, "Subject", "Meeting")
                    start = getattr(item, "Start", None)
                    end = getattr(item, "End", None)
                    location = getattr(item, "Location", "")
                    organizer = getattr(item, "Organizer", "")
                    req_attendees = getattr(item, "RequiredAttendees", "")
                    entry_id = getattr(item, "EntryID", f"mapi-{hash(subject)}")

                    st_dt = start if isinstance(start, datetime) else now_dt
                    et_dt = end if isinstance(end, datetime) else (st_dt + timedelta(minutes=30))
                    if st_dt.tzinfo is None:
                        st_dt = st_dt.replace(tzinfo=timezone.utc)
                    if et_dt.tzinfo is None:
                        et_dt = et_dt.replace(tzinfo=timezone.utc)

                    attendees_list = [a.strip() for a in req_attendees.split(";") if a.strip()]

                    meetings.append(
                        CalendarMeeting(
                            id=str(entry_id),
                            title=str(subject),
                            start_time=st_dt,
                            end_time=et_dt,
                            location=str(location),
                            join_url=None,
                            attendees=attendees_list,
                            organizer=str(organizer),
                            is_online="teams" in location.lower() or "zoom" in location.lower(),
                        )
                    )
                except Exception as item_exc:
                    logger.debug("Failed parsing MAPI calendar item: %s", item_exc)
                    continue

            self._status["is_healthy"] = True
            self._status["error_state"] = None
            return meetings

        except Exception as exc:
            logger.error("Failed querying Outlook MAPI items: %s", exc)
            self._status["is_healthy"] = False
            self._status["error_state"] = "MAPI_QUERY_ERROR"
            return []

    def get_provider_status(self) -> Dict[str, Any]:
        """Return provider health status dictionary."""
        return dict(self._status)
