"""
VoxLocal Calendar Integration Package.
Provides unified calendar connectivity via:
- MS Graph API OAuth 2.0 PKCE & Device Code Flow (RFC 8628) with Windows DPAPI encryption
- Google Calendar API v3 with Google OAuth 2.0 PKCE
- Zoom Meetings API with Zoom OAuth 2.0
- Outlook / M365 Published ICS calendar feeds
- Local Windows Outlook desktop MAPI COM fallback
- Deterministic Mock provider for automated testing
- UnifiedCalendarEngine multi-provider aggregator with cross-calendar deduplication
"""

from voxlocal.calendar.base import (
    BaseCalendarProvider,
    CalendarMeeting,
    CalendarProviderType,
)
from voxlocal.calendar.calendar_manager import (
    CalendarAlertEvaluator,
    CalendarManager,
)
from voxlocal.calendar.google_provider import GoogleCalendarProvider
from voxlocal.calendar.graph_provider import MsGraphCalendarProvider
from voxlocal.calendar.ics_provider import IcsCalendarProvider
from voxlocal.calendar.mapi_provider import LocalMapiCalendarProvider
from voxlocal.calendar.mock_provider import MockCalendarProvider
from voxlocal.calendar.unified_engine import UnifiedCalendarEngine
from voxlocal.calendar.zoom_provider import ZoomCalendarProvider

__all__ = [
    "BaseCalendarProvider",
    "CalendarMeeting",
    "CalendarProviderType",
    "MockCalendarProvider",
    "MsGraphCalendarProvider",
    "GoogleCalendarProvider",
    "ZoomCalendarProvider",
    "IcsCalendarProvider",
    "LocalMapiCalendarProvider",
    "CalendarAlertEvaluator",
    "CalendarManager",
    "UnifiedCalendarEngine",
]
