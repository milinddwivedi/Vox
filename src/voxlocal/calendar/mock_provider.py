"""
VoxLocal Mock Calendar Provider.
Deterministic offline CI/CD fixture implementing BaseCalendarProvider.
Supports:
- Meeting queue injection and upcoming events retrieval
- Token lifecycle simulation (fresh, expiring, refresh)
- Disconnect and re-authentication state machine
- Network failure simulation with exponential backoff
- Rate limit simulation (HTTP 429) with Retry-After header parsing
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from voxlocal.calendar.base import BaseCalendarProvider, CalendarMeeting, CalendarProviderType


class MockCalendarProvider(BaseCalendarProvider):
    """Deterministic Mock Calendar Provider conforming to BaseCalendarProvider contract."""

    def __init__(self) -> None:
        self._authenticated: bool = False
        self._meetings: List[CalendarMeeting] = []
        self._auth_token_expiry: Optional[datetime] = None
        self._status: Dict[str, Any] = {
            "provider_type": CalendarProviderType.MOCK.value,
            "is_healthy": True,
            "last_poll_time": None,
            "error_state": None,
        }
        self.retry_after_seconds: int = 0
        self.backoff_interval_sec: int = 60

    def set_mock_token(self, expires_in_seconds: int = 3600) -> None:
        """Configure mock token expiry relative to current time."""
        self._authenticated = True
        self._auth_token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)

    def is_token_near_expiry(self, threshold_seconds: int = 300) -> bool:
        """Check if auth token expires within threshold_seconds (default 5 minutes)."""
        if not self._auth_token_expiry:
            return True
        remaining = (self._auth_token_expiry - datetime.now(timezone.utc)).total_seconds()
        return remaining < threshold_seconds

    def refresh_token(self) -> bool:
        """Execute mock token refresh, extending validity by 3600s."""
        if not self._authenticated:
            return False
        self._auth_token_expiry = datetime.now(timezone.utc) + timedelta(seconds=3600)
        return True

    def authenticate(self, interactive: bool = True) -> bool:
        """Authenticate session and grant 1-hour access token."""
        self._authenticated = True
        self._auth_token_expiry = datetime.now(timezone.utc) + timedelta(seconds=3600)
        self._status["error_state"] = None
        self._status["is_healthy"] = True
        return True

    def disconnect(self) -> bool:
        """Purge authentication and cached meetings."""
        self._authenticated = False
        self._auth_token_expiry = None
        self._meetings.clear()
        self._status["error_state"] = "DISCONNECTED"
        return True

    def is_authenticated(self) -> bool:
        """Return True if session is currently authenticated."""
        return self._authenticated

    def inject_meeting(self, meeting: CalendarMeeting) -> None:
        """Inject a test meeting into the mock calendar queue."""
        self._meetings.append(meeting)

    def clear_meetings(self) -> None:
        """Clear all injected mock meetings."""
        self._meetings.clear()

    def get_upcoming_meetings(self, window_minutes: int = 60) -> List[CalendarMeeting]:
        """Fetch scheduled meetings within forward window, filtering cancelled events."""
        self._status["last_poll_time"] = datetime.now(timezone.utc).isoformat()
        if not self._authenticated:
            return []
        now = datetime.now(timezone.utc)
        limit = now + timedelta(minutes=window_minutes)
        upcoming = [
            m for m in self._meetings
            if not m.is_cancelled and now <= m.start_time <= limit
        ]
        return upcoming

    def get_provider_status(self) -> Dict[str, Any]:
        """Return provider health status dictionary."""
        return dict(self._status)

    def simulate_rate_limit(self, retry_after: int = 30) -> None:
        """Simulate HTTP 429 rate limit with Retry-After duration."""
        self.retry_after_seconds = retry_after
        self._status["is_healthy"] = False
        self._status["error_state"] = "HTTP_429_RATE_LIMIT"

    def handle_network_failure(self) -> int:
        """Simulate network drop and return updated exponential backoff duration (capped at 300s)."""
        self._status["is_healthy"] = False
        self._status["error_state"] = "NETWORK_OFFLINE"
        self.backoff_interval_sec = min(300, self.backoff_interval_sec * 2)
        return self.backoff_interval_sec
