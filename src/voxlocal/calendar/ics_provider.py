"""
VoxLocal ICS Calendar Feed Provider.
Zero-admin-approval Outlook / Microsoft 365 / Google Calendar support via:
- Published Outlook Web / New Outlook .ics subscription URLs (webcal://, https://)
- Local .ics calendar files
- Windows timezone name mapping + Teams / Zoom / Google Meet join URL extraction
- Encrypted feed URI persistence using Windows DPAPI
"""

from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from voxlocal.calendar.base import BaseCalendarProvider, CalendarMeeting, CalendarProviderType
from voxlocal.calendar.graph_provider import dpapi_decrypt, dpapi_encrypt
from voxlocal.core.logger import get_logger

logger = get_logger("calendar.ics")

WINDOWS_TZ_TO_IANA: Dict[str, str] = {
    "India Standard Time": "Asia/Kolkata",
    "UTC": "UTC",
    "Coordinated Universal Time": "UTC",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central European Standard Time": "Europe/Warsaw",
    "Romance Standard Time": "Europe/Paris",
    "Eastern Standard Time": "America/New_York",
    "US Eastern Standard Time": "America/Indiana/Indianapolis",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
    "Singapore Standard Time": "Asia/Singapore",
    "Tokyo Standard Time": "Asia/Tokyo",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "Arabian Standard Time": "Asia/Dubai",
}

_MEETING_URL_RE = re.compile(
    r"https?://(?:teams\.microsoft\.com|teams\.live\.com|[\w.-]*zoom\.us|meet\.google\.com)/[^\s<>\"'\\]+",
    re.IGNORECASE,
)

_WEEKDAY_MAP = {
    "MO": 0,
    "TU": 1,
    "WE": 2,
    "TH": 3,
    "FR": 4,
    "SA": 5,
    "SU": 6,
}


def _unescape_ics_text(val: str) -> str:
    return (
        val.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
        .strip()
    )


def _resolve_tz(tzid: Optional[str]) -> Any:
    if not tzid:
        return timezone.utc
    clean = tzid.strip().strip('"')
    iana = WINDOWS_TZ_TO_IANA.get(clean, clean)
    try:
        return ZoneInfo(iana)
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc


def _parse_ics_datetime(raw_value: str, tzid: Optional[str] = None) -> Optional[datetime]:
    val = raw_value.strip()
    if not val:
        return None
    if val.endswith("Z"):
        try:
            dt = datetime.strptime(val, "%Y%m%dT%H%M%SZ")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if "T" in val:
        for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
            try:
                naive = datetime.strptime(val, fmt)
                tz = _resolve_tz(tzid) if tzid else (datetime.now().astimezone().tzinfo or timezone.utc)
                return naive.replace(tzinfo=tz).astimezone(timezone.utc)
            except ValueError:
                continue
        return None
    try:
        d = datetime.strptime(val, "%Y%m%d").date()
        tz = _resolve_tz(tzid) if tzid else (datetime.now().astimezone().tzinfo or timezone.utc)
        return datetime.combine(d, dt_time.min).replace(tzinfo=tz).astimezone(timezone.utc)
    except ValueError:
        return None


def _parse_duration_iso(raw_dur: str) -> Optional[timedelta]:
    m = re.match(r"^P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$", raw_dur.strip())
    if not m:
        return None
    weeks, days, hours, mins, secs = (int(x or 0) for x in m.groups())
    return timedelta(weeks=weeks, days=days, hours=hours, minutes=mins, seconds=secs)


def parse_ics_events(
    ics_content: str,
    window_start: datetime,
    window_end: datetime,
) -> tuple[str, List[CalendarMeeting]]:
    """
    Parse RFC 5545 VCALENDAR text into CalendarMeeting instances falling within
    [window_start - 15m, window_end], returning (calendar_name, meetings).
    """
    unfolded = re.sub(r"\r?\n[ \t]", "", ics_content)
    lines = unfolded.splitlines()

    cal_name = "Outlook ICS Calendar"
    in_event = False
    cur: Dict[str, Any] = {}
    attendees: List[str] = []
    meetings: List[CalendarMeeting] = []
    search_start = window_start - timedelta(minutes=15)

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper == "BEGIN:VEVENT":
            in_event = True
            cur = {}
            attendees = []
            continue
        if upper == "END:VEVENT":
            in_event = False
            dtstart: Optional[datetime] = cur.get("dtstart")
            if dtstart is not None:
                dtend: Optional[datetime] = cur.get("dtend")
                if dtend is None:
                    dur = cur.get("duration") or timedelta(minutes=30)
                    dtend = dtstart + dur
                ev_dur = max(timedelta(minutes=5), dtend - dtstart)

                occ_starts = _expand_rrule_occurrences(
                    dtstart=dtstart,
                    rrule=cur.get("rrule", ""),
                    search_start=search_start,
                    search_end=window_end,
                    exdates=cur.get("exdates", set()),
                )

                join_url = cur.get("join_url")
                if not join_url:
                    haystack = f"{cur.get('location', '')}\n{cur.get('description', '')}"
                    m_url = _MEETING_URL_RE.search(haystack)
                    if m_url:
                        join_url = m_url.group(0).rstrip(".,;)")

                loc = cur.get("location", "") or ("Online Meeting" if join_url else "")
                is_cancelled = cur.get("status", "").upper() == "CANCELLED"
                is_online = bool(join_url) or any(
                    kw in loc.lower() for kw in ("teams", "zoom", "meet.google", "webex")
                )
                uid = cur.get("uid") or f"ics-{abs(hash((cur.get('summary', ''), dtstart.isoformat())))}"
                for idx, occ_s in enumerate(occ_starts):
                    occ_e = occ_s + ev_dur
                    if occ_e < search_start or occ_s > window_end:
                        continue
                    occ_id = uid if idx == 0 else f"{uid}_{int(occ_s.timestamp())}"
                    meetings.append(
                        CalendarMeeting(
                            id=occ_id,
                            title=cur.get("summary") or "Untitled Meeting",
                            start_time=occ_s,
                            end_time=occ_e,
                            location=loc,
                            join_url=join_url,
                            attendees=list(attendees),
                            organizer=cur.get("organizer", ""),
                            is_online=is_online,
                            is_cancelled=is_cancelled,
                            sensitivity=cur.get("class", "normal").lower(),
                        )
                    )
            cur = {}
            attendees = []
            continue

        if ":" not in stripped:
            continue
        prop_part, raw_val = stripped.split(":", 1)
        prop_segs = prop_part.split(";")
        prop_name = prop_segs[0].upper()
        params: Dict[str, str] = {}
        for p in prop_segs[1:]:
            if "=" in p:
                pk, pv = p.split("=", 1)
                params[pk.upper()] = pv.strip().strip('"')

        if not in_event:
            if prop_name in ("X-WR-CALNAME", "NAME"):
                cal_name = _unescape_ics_text(raw_val) or cal_name
            continue

        if prop_name == "UID":
            cur["uid"] = raw_val.strip()
        elif prop_name == "SUMMARY":
            cur["summary"] = _unescape_ics_text(raw_val)
        elif prop_name == "LOCATION":
            cur["location"] = _unescape_ics_text(raw_val)
        elif prop_name == "DESCRIPTION":
            cur["description"] = _unescape_ics_text(raw_val)
        elif prop_name == "STATUS":
            cur["status"] = raw_val.strip()
        elif prop_name == "CLASS":
            cur["class"] = raw_val.strip()
        elif prop_name == "X-MICROSOFT-SKYPETEAMSMEETINGURL":
            cur["join_url"] = raw_val.strip()
        elif prop_name == "DTSTART":
            cur["dtstart"] = _parse_ics_datetime(raw_val, params.get("TZID"))
        elif prop_name == "DTEND":
            cur["dtend"] = _parse_ics_datetime(raw_val, params.get("TZID"))
        elif prop_name == "DURATION":
            cur["duration"] = _parse_duration_iso(raw_val)
        elif prop_name == "RRULE":
            cur["rrule"] = raw_val.strip()
        elif prop_name == "EXDATE":
            ex_set = cur.setdefault("exdates", set())
            for part in raw_val.split(","):
                parsed_ex = _parse_ics_datetime(part, params.get("TZID"))
                if parsed_ex is not None:
                    ex_set.add(int(parsed_ex.timestamp()))
        elif prop_name == "ORGANIZER":
            org = params.get("CN") or raw_val.replace("mailto:", "").replace("MAILTO:", "")
            cur["organizer"] = _unescape_ics_text(org)
        elif prop_name == "ATTENDEE":
            att = params.get("CN") or raw_val.replace("mailto:", "").replace("MAILTO:", "")
            att_clean = _unescape_ics_text(att)
            if att_clean and att_clean not in attendees:
                attendees.append(att_clean)

    meetings.sort(key=lambda m: m.start_time)
    return cal_name, meetings


def _expand_rrule_occurrences(
    dtstart: datetime,
    rrule: str,
    search_start: datetime,
    search_end: datetime,
    exdates: set[int],
) -> List[datetime]:
    if not rrule:
        return [] if int(dtstart.timestamp()) in exdates else [dtstart]

    parts = {}
    for kv in rrule.split(";"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            parts[k.upper()] = v.upper()

    freq = parts.get("FREQ", "")
    interval = max(1, int(parts.get("INTERVAL", "1") or "1"))
    until_dt = _parse_ics_datetime(parts["UNTIL"]) if "UNTIL" in parts else None
    max_count = int(parts["COUNT"]) if "COUNT" in parts and parts["COUNT"].isdigit() else None

    if dtstart > search_end:
        return []

    hard_end = search_end if until_dt is None else min(search_end, until_dt)
    results: List[datetime] = []

    if freq == "DAILY":
        step = timedelta(days=interval)
        cur = dtstart
        idx = 0
        while cur <= hard_end and idx < 1500:
            if max_count is not None and idx >= max_count:
                break
            if cur >= search_start and int(cur.timestamp()) not in exdates:
                results.append(cur)
            cur += step
            idx += 1
        return results

    if freq == "WEEKLY":
        byday_raw = parts.get("BYDAY", "")
        target_days = [
            _WEEKDAY_MAP[d[-2:]]
            for d in byday_raw.split(",")
            if len(d) >= 2 and d[-2:] in _WEEKDAY_MAP
        ]
        if not target_days:
            target_days = [dtstart.weekday()]

        # Jump close to search_start minus 1 week to keep expansion instant
        if dtstart < search_start - timedelta(days=14) and max_count is None:
            weeks_diff = max(0, ((search_start - dtstart).days // 7) // interval - 1)
            cur_week = dtstart + timedelta(weeks=weeks_diff * interval)
            idx = 0
        else:
            cur_week = dtstart
            idx = 0

        week_start_anchor = cur_week - timedelta(days=cur_week.weekday())
        for _ in range(400):
            for wd in sorted(set(target_days)):
                cand_date = week_start_anchor.date() + timedelta(days=wd)
                cand = datetime.combine(cand_date, dtstart.timetz())
                if cand < dtstart:
                    continue
                if cand > hard_end:
                    return results
                if max_count is not None and idx >= max_count:
                    return results
                idx += 1
                if cand >= search_start and int(cand.timestamp()) not in exdates:
                    results.append(cand)
            week_start_anchor += timedelta(weeks=interval)
            if week_start_anchor > hard_end + timedelta(days=7):
                break
        return results

    return [] if int(dtstart.timestamp()) in exdates else [dtstart]


class IcsCalendarProvider(BaseCalendarProvider):
    """Outlook / Microsoft 365 / iCalendar (.ics URL or file) calendar provider."""

    def __init__(
        self,
        feed_url: str = "",
        auth_file_path: Optional[Path] = None,
    ) -> None:
        self.auth_file_path = auth_file_path or (Path("data") / "calendar_ics.dat")
        self._feed_url: str = self._normalize_url(feed_url)
        self._calendar_name: str = ""
        self._last_ics_text: str = ""
        self._authenticated: bool = False
        self._status: Dict[str, Any] = {
            "provider_type": CalendarProviderType.LOCAL_MAPI.value,
            "is_healthy": True,
            "last_poll_time": None,
            "error_state": None,
            "error_detail": None,
            "account_upn": None,
            "source_mode": "ics",
        }
        if not self._feed_url:
            self._load_cached_config()

    @staticmethod
    def _normalize_url(raw: str) -> str:
        val = (raw or "").strip()
        if val.lower().startswith("webcal://"):
            return "https://" + val[len("webcal://") :]
        return val

    def _load_cached_config(self) -> None:
        if not self.auth_file_path.exists():
            return
        try:
            raw = dpapi_decrypt(self.auth_file_path.read_bytes())
            data = json.loads(raw.decode("utf-8"))
            self._feed_url = self._normalize_url(data.get("feed_url", ""))
            self._calendar_name = str(data.get("calendar_name", "") or "")
            if self._feed_url:
                self._authenticated = True
                self._status["account_upn"] = self._calendar_name or self._display_source_label()
        except Exception as exc:
            logger.debug("Could not load cached ICS calendar config: %s", exc)

    def _save_config(self) -> None:
        try:
            self.auth_file_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({
                "feed_url": self._feed_url,
                "calendar_name": self._calendar_name,
            }).encode("utf-8")
            self.auth_file_path.write_bytes(dpapi_encrypt(payload, "VoxLocal ICS Calendar"))
        except Exception as exc:
            logger.error("Failed saving encrypted ICS calendar config: %s", exc)

    def _display_source_label(self) -> str:
        if self._calendar_name:
            return self._calendar_name
        if "outlook" in self._feed_url.lower() or "office365" in self._feed_url.lower():
            return "Outlook ICS Calendar"
        return Path(self._feed_url).name if self._feed_url and not self._feed_url.startswith("http") else "ICS Calendar Feed"

    def _fetch_ics_text(self) -> str:
        target = self._feed_url
        if not target:
            raise ValueError("No ICS calendar link or file path provided.")
        if target.lower().startswith(("http://", "https://")):
            req = urllib.request.Request(target, method="GET")
            req.add_header("User-Agent", "VOX-Desktop/2.0 (Calendar Sync)")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", errors="replace")
        path = Path(target)
        if not path.exists():
            raise FileNotFoundError(f"ICS file not found: {path}")
        return path.read_text(encoding="utf-8", errors="replace")

    def authenticate(self, interactive: bool = True) -> bool:
        if not self._feed_url:
            self._authenticated = False
            self._status["error_state"] = "AUTH_REQUIRED"
            self._status["error_detail"] = "Enter your Outlook/M365 .ics URL or choose a local .ics file."
            return False
        try:
            raw_ics = self._fetch_ics_text()
            if "BEGIN:VCALENDAR" not in raw_ics.upper():
                raise ValueError("URL or file is not a valid iCalendar (.ics) feed (missing BEGIN:VCALENDAR).")
            self._last_ics_text = raw_ics
            now_dt = datetime.now(timezone.utc)
            cal_name, _ = parse_ics_events(raw_ics, now_dt, now_dt + timedelta(hours=24))
            self._calendar_name = cal_name
            self._authenticated = True
            self._status["is_healthy"] = True
            self._status["error_state"] = None
            self._status["error_detail"] = None
            self._status["account_upn"] = self._display_source_label()
            self._save_config()
            return True
        except Exception as exc:
            self._authenticated = False
            self._status["is_healthy"] = False
            self._status["error_state"] = "ICS_FETCH_FAILED"
            self._status["error_detail"] = str(exc)
            logger.warning("ICS calendar validation failed: %s", exc)
            return False

    def is_authenticated(self) -> bool:
        return bool(self._authenticated and self._feed_url)

    def disconnect(self) -> bool:
        self._authenticated = False
        self._feed_url = ""
        self._last_ics_text = ""
        self._calendar_name = ""
        try:
            if self.auth_file_path.exists():
                self.auth_file_path.unlink()
        except Exception:
            pass
        self._status["is_healthy"] = True
        self._status["error_state"] = "DISCONNECTED"
        self._status["error_detail"] = None
        self._status["account_upn"] = None
        return True

    def get_upcoming_meetings(self, window_minutes: int = 60) -> List[CalendarMeeting]:
        self._status["last_poll_time"] = datetime.now(timezone.utc).isoformat()
        if not self.is_authenticated():
            return []
        try:
            raw_ics = self._fetch_ics_text()
            self._last_ics_text = raw_ics
            self._status["is_healthy"] = True
            self._status["error_state"] = None
        except Exception as exc:
            logger.warning("ICS refresh failed (%s); using cached events if available.", exc)
            self._status["is_healthy"] = False
            self._status["error_state"] = "NETWORK_OFFLINE"
            self._status["error_detail"] = str(exc)
            raw_ics = self._last_ics_text
            if not raw_ics:
                return []

        now_dt = datetime.now(timezone.utc)
        end_dt = now_dt + timedelta(minutes=window_minutes)
        cal_name, meetings = parse_ics_events(raw_ics, now_dt, end_dt)
        if cal_name:
            self._calendar_name = cal_name
            self._status["account_upn"] = self._display_source_label()
        return meetings

    def get_provider_status(self) -> Dict[str, Any]:
        return dict(self._status)
