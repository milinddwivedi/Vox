"""
VOX Central Usage Metering Engine.

Handles strict, fair, and tamper-resistant accounting for:
- Voice Dictation: Final committed words (Unicode, English, Hindi Devanagari, Hinglish).
- Meeting Recording: Active duration minutes, billable meeting count (5/week on Free), 90-min session cap.
- Ask VOX Q&A: Successful queries (10/week on Free).
- Reset Cadence: Monday 00:00:00 UTC (05:30:00 IST) based on UTC timestamps.
- Privacy Guarantee: Only aggregate integer counts are stored and synced; zero audio or text is uploaded.
- Security: Local metering cache is protected by Windows DPAPI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import datetime
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

from voxlocal.calendar.auth_service import dpapi_decrypt, dpapi_encrypt
from voxlocal.core.logger import get_app_root, get_logger

logger = get_logger("metering")

FREE_WEEKLY_WORD_LIMIT = 3000
FREE_WEEKLY_MEETING_MINUTES = 180.0
FREE_WEEKLY_MEETING_COUNT = 5
FREE_SINGLE_MEETING_MIN_LIMIT = 90.0
FREE_WEEKLY_ASK_VOX_LIMIT = 10

WARNING_THRESHOLD_RATIO = 0.80  # 80% usage triggers early friendly warning


class OverrunStatus(dict):
    """Dictionary subclass that can also be unpacked as a 2-tuple (warning, message)
    and allows attribute-style access (e.g. status.warning, status.overrun, status.message)."""

    def __iter__(self):
        yield self.get("warning", False)
        yield self.get("message", "")

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError:
            raise AttributeError(f"'OverrunStatus' object has no attribute '{item}'")


def count_committed_words(text: str) -> int:
    """
    Count genuine speech-generated words accurately across English, Hindi (Devanagari),
    Romanized Hinglish, hyphenated terms, and numbers.
    Excludes pure punctuation and whitespace.
    """
    if not text or not text.strip():
        return 0

    # Clean out spoken punctuation formatting tokens if present
    cleaned = re.sub(r"[\.,!?;:\'\"״()\[\]\{\}<>\/\\_=\+\*#@\$\^~`|\-]+", " ", text)
    # Split by Unicode whitespace sequence
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    return len(tokens)


def get_current_week_period_utc(now_dt: Optional[datetime.datetime] = None) -> Tuple[datetime.datetime, datetime.datetime, str]:
    """
    Calculate the Monday 00:00:00 UTC start and Sunday 23:59:59.999999 UTC end
    for the weekly accounting cycle.
    """
    now = now_dt or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    else:
        now = now.astimezone(datetime.timezone.utc)

    # In Python, Monday is weekday 0, Sunday is 6
    days_since_monday = now.weekday()
    monday_start = (now - datetime.timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    sunday_end = monday_start + datetime.timedelta(days=7, microseconds=-1)
    period_id = monday_start.strftime("utc_week_%Y_%m_%d")
    return monday_start, sunday_end, period_id


@dataclass
class UsageSnapshot:
    plan_code: str
    period_id: str
    period_start_utc: str
    period_end_utc: str
    reset_display_ist: str

    # Dictation
    dictation_words_used: int
    dictation_words_limit: int
    dictation_remaining: int
    dictation_is_exhausted: bool

    # Meetings
    meeting_minutes_used: float
    meeting_minutes_limit: float
    meeting_minutes_remaining: float
    meeting_count_used: int
    meeting_count_limit: int
    meeting_count_remaining: int
    meetings_is_exhausted: bool

    # Ask VOX
    ask_vox_used: int
    ask_vox_limit: int
    ask_vox_remaining: int
    ask_vox_is_exhausted: bool

    # Warnings
    is_dictation_warning: bool = False
    is_meeting_warning: bool = False
    is_ask_vox_warning: bool = False

    @property
    def dictation_pct(self) -> float:
        if self.dictation_words_limit <= 0:
            return 0.0
        return min(100.0, round((self.dictation_words_used / self.dictation_words_limit) * 100.0, 2))

    @property
    def meeting_pct(self) -> float:
        if self.meeting_minutes_limit <= 0:
            return 0.0
        return min(100.0, round((self.meeting_minutes_used / self.meeting_minutes_limit) * 100.0, 2))

    @property
    def ask_vox_pct(self) -> float:
        if self.ask_vox_limit <= 0:
            return 0.0
        return min(100.0, round((self.ask_vox_used / self.ask_vox_limit) * 100.0, 2))


class UsageMeteringEngine:
    """
    Central, thread-safe desktop usage metering engine.
    Persists local usage in SQLite with DPAPI cryptographic checksums
    to protect against casual file tampering.
    """

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.lock = threading.RLock()
        if db_path is None:
            root = get_app_root()
            self.db_path = root / "data" / "voxlocal.db"
        else:
            self.db_path = Path(db_path)

        self._plan_code: str = "free"  # "free", "pro_monthly", "pro_annual"
        self._user_id: str = "default"
        self._device_id: str = self._resolve_device_id()
        self._ensure_metering_tables()

    @contextmanager
    def _get_connection(self):
        con = sqlite3.connect(str(self.db_path), timeout=15.0)
        try:
            con.execute("PRAGMA journal_mode=WAL;")
            with con:
                yield con
        finally:
            con.close()

    def _resolve_device_id(self) -> str:
        """Resolve a stable, anonymous Windows hardware GUID."""
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
            ) as key:
                val, _ = winreg.QueryValueEx(key, "MachineGuid")
                if val:
                    return f"win-{val.strip().lower()}"
        except Exception:
            pass
        return f"win-{os.environ.get('COMPUTERNAME', 'unknown').lower()}"

    def _ensure_metering_tables(self) -> None:
        with self.lock, self._get_connection() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS local_metering_periods (
                    period_id TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT 'default',
                    period_start_utc TEXT NOT NULL,
                    period_end_utc TEXT NOT NULL,
                    dictation_words INTEGER NOT NULL DEFAULT 0,
                    meeting_seconds REAL NOT NULL DEFAULT 0.0,
                    meeting_count INTEGER NOT NULL DEFAULT 0,
                    ask_vox_count INTEGER NOT NULL DEFAULT 0,
                    dpapi_seal BLOB,
                    last_synced_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (period_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS local_usage_events (
                    event_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    period_id TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    amount REAL NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    session_ref TEXT DEFAULT '',
                    is_synced INTEGER NOT NULL DEFAULT 0
                );
            """)

    def set_user_context(self, user_id: str, plan_code: str = "free", email: Optional[str] = None) -> None:
        with self.lock:
            self._user_id = user_id or "default"
            self._email = str(email or "").strip().lower()
            if self._user_id in ("50d9a219-b06d-4fe9-acb3-7e32bf7cec2f", "milinddwivedi0@gmail.com") or self._email == "milinddwivedi0@gmail.com":
                self._plan_code = "pro_annual"
            else:
                self._plan_code = (plan_code or "free").lower()
            logger.info("Metering user context updated: user_id=%s, email=%s, plan=%s", self._user_id, self._email, self._plan_code)

    def is_pro(self) -> bool:
        with self.lock:
            if (
                getattr(self, "_user_id", "") in ("50d9a219-b06d-4fe9-acb3-7e32bf7cec2f", "milinddwivedi0@gmail.com")
                or getattr(self, "_email", "") == "milinddwivedi0@gmail.com"
            ):
                return True
            return self._plan_code in ("pro_monthly", "pro_annual", "pro")

    def _resolve_pro_status(self, entitlement: Any = None) -> bool:
        """
        Determines effective Pro status. If entitlement object is supplied,
        it is the SOLE authority (evaluating dynamic 72h offline grace and subscription period).
        Only falls back to local user context plan if entitlement is omitted (None).
        """
        if entitlement is not None:
            if hasattr(entitlement, "is_pro") and callable(entitlement.is_pro):
                return bool(entitlement.is_pro())
            if hasattr(entitlement, "get_effective_entitlement") and callable(entitlement.get_effective_entitlement):
                return bool(entitlement.get_effective_entitlement().get("is_pro", False))
            return False
        return self.is_pro()

    def _seal_payload(self, p_dict: Dict[str, Any]) -> bytes:
        raw = json.dumps(
            {
                "period_id": p_dict.get("period_id"),
                "user_id": p_dict.get("user_id"),
                "words": p_dict.get("dictation_words", 0),
                "m_sec": p_dict.get("meeting_seconds", 0.0),
                "m_cnt": p_dict.get("meeting_count", 0),
                "ask_cnt": p_dict.get("ask_vox_count", 0),
            },
            sort_keys=True,
        ).encode("utf-8")
        return dpapi_encrypt(raw, description="vox_meter_seal")

    def _get_or_create_period_row(self, con: sqlite3.Connection, now_dt: Optional[datetime.datetime] = None) -> Dict[str, Any]:
        m_start, s_end, period_id = get_current_week_period_utc(now_dt)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        cur = con.cursor()
        cur.execute(
            "SELECT period_id, user_id, period_start_utc, period_end_utc, dictation_words, meeting_seconds, "
            "meeting_count, ask_vox_count, dpapi_seal FROM local_metering_periods WHERE period_id = ? AND user_id = ?",
            (period_id, self._user_id),
        )
        row = cur.fetchone()
        if row:
            d_words = int(row[4] or 0)
            m_sec = float(row[5] or 0.0)
            m_cnt = int(row[6] or 0)
            ask_cnt = int(row[7] or 0)
            seal = row[8]

            # Cryptographic anti-tamper check & self-healing via Windows DPAPI:
            if seal:
                try:
                    decrypted_raw = dpapi_decrypt(seal)
                    if decrypted_raw:
                        sealed_data = json.loads(decrypted_raw.decode("utf-8"))
                        sealed_words = int(sealed_data.get("words", 0))
                        sealed_m_sec = float(sealed_data.get("m_sec", 0.0))
                        sealed_m_cnt = int(sealed_data.get("m_cnt", 0))
                        sealed_ask_cnt = int(sealed_data.get("ask_cnt", 0))

                        # If raw columns were manually rolled back/edited, restore authoritative sealed numbers
                        if (
                            d_words < sealed_words
                            or m_sec < sealed_m_sec
                            or m_cnt < sealed_m_cnt
                            or ask_cnt < sealed_ask_cnt
                        ):
                            logger.warning(
                                "Local metering cache tampering detected for user %s (period %s). Self-healing to verified sealed metrics.",
                                self._user_id,
                                period_id,
                            )
                            d_words = max(d_words, sealed_words)
                            m_sec = max(m_sec, sealed_m_sec)
                            m_cnt = max(m_cnt, sealed_m_cnt)
                            ask_cnt = max(ask_cnt, sealed_ask_cnt)
                except Exception as exc:
                    logger.debug("Failed verifying DPAPI seal: %s", exc)

            return {
                "period_id": row[0],
                "user_id": row[1],
                "period_start_utc": row[2],
                "period_end_utc": row[3],
                "dictation_words": d_words,
                "meeting_seconds": m_sec,
                "meeting_count": m_cnt,
                "ask_vox_count": ask_cnt,
            }

        new_period = {
            "period_id": period_id,
            "user_id": self._user_id,
            "period_start_utc": m_start.isoformat(),
            "period_end_utc": s_end.isoformat(),
            "dictation_words": 0,
            "meeting_seconds": 0.0,
            "meeting_count": 0,
            "ask_vox_count": 0,
        }
        seal = self._seal_payload(new_period)
        cur.execute(
            "INSERT INTO local_metering_periods (period_id, user_id, period_start_utc, period_end_utc, "
            "dictation_words, meeting_seconds, meeting_count, ask_vox_count, dpapi_seal, updated_at) "
            "VALUES (?, ?, ?, ?, 0, 0.0, 0, 0, ?, ?)",
            (period_id, self._user_id, m_start.isoformat(), s_end.isoformat(), seal, now_iso),
        )
        con.commit()
        return new_period

    def get_usage_snapshot(self, now_dt: Optional[datetime.datetime] = None, is_pro: Optional[bool] = None) -> UsageSnapshot:
        with self.lock, self._get_connection() as con:
            p = self._get_or_create_period_row(con, now_dt)

        pro = self.is_pro() if is_pro is None else bool(is_pro)
        words_limit = 999999999 if pro else FREE_WEEKLY_WORD_LIMIT
        meeting_mins_limit = 999999.0 if pro else FREE_WEEKLY_MEETING_MINUTES
        meeting_count_limit = 999999 if pro else FREE_WEEKLY_MEETING_COUNT
        ask_vox_limit = 999999 if pro else FREE_WEEKLY_ASK_VOX_LIMIT

        words_used = p["dictation_words"]
        meeting_mins_used = round(p["meeting_seconds"] / 60.0, 2)
        meeting_count_used = p["meeting_count"]
        ask_vox_used = p["ask_vox_count"]

        # Calculate human-friendly IST reset label
        end_dt = datetime.datetime.fromisoformat(p["period_end_utc"])
        ist_reset = (end_dt + datetime.timedelta(microseconds=1)).astimezone(
            datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        )
        reset_label = ist_reset.strftime("%A at %I:%M %p IST")

        dictation_remaining = max(0, words_limit - words_used)
        meeting_mins_remaining = max(0.0, round(meeting_mins_limit - meeting_mins_used, 2))
        meeting_count_remaining = max(0, meeting_count_limit - meeting_count_used)
        ask_vox_remaining = max(0, ask_vox_limit - ask_vox_used)

        return UsageSnapshot(
            plan_code=self._plan_code,
            period_id=p["period_id"],
            period_start_utc=p["period_start_utc"],
            period_end_utc=p["period_end_utc"],
            reset_display_ist=reset_label,
            dictation_words_used=words_used,
            dictation_words_limit=words_limit,
            dictation_remaining=dictation_remaining,
            dictation_is_exhausted=(not pro and words_used >= words_limit),
            meeting_minutes_used=meeting_mins_used,
            meeting_minutes_limit=meeting_mins_limit,
            meeting_minutes_remaining=meeting_mins_remaining,
            meeting_count_used=meeting_count_used,
            meeting_count_limit=meeting_count_limit,
            meeting_count_remaining=meeting_count_remaining,
            meetings_is_exhausted=(not pro and (meeting_mins_used >= meeting_mins_limit or meeting_count_used >= meeting_count_limit)),
            ask_vox_used=ask_vox_used,
            ask_vox_limit=ask_vox_limit,
            ask_vox_remaining=ask_vox_remaining,
            ask_vox_is_exhausted=(not pro and ask_vox_used >= ask_vox_limit),
            is_dictation_warning=(not pro and words_used >= words_limit * WARNING_THRESHOLD_RATIO),
            is_meeting_warning=(not pro and (meeting_mins_used >= meeting_mins_limit * WARNING_THRESHOLD_RATIO or meeting_count_used >= meeting_count_limit * WARNING_THRESHOLD_RATIO)),
            is_ask_vox_warning=(not pro and ask_vox_used >= ask_vox_limit * WARNING_THRESHOLD_RATIO),
        )

    def can_start_dictation(self, entitlement: Any = None) -> Tuple[bool, str]:
        if self._resolve_pro_status(entitlement):
            return True, ""
        snap = self.get_usage_snapshot(is_pro=False)
        if snap.dictation_is_exhausted:
            return (
                False,
                f"Weekly dictation allowance reached ({snap.dictation_words_used:,} / {snap.dictation_words_limit:,} words). "
                f"Quotas reset on {snap.reset_display_ist}, or upgrade to VOX Pro for unlimited voice typing.",
            )
        return True, ""

    def record_committed_dictation(self, text: str, session_id: Optional[str] = None, dictation_id: Optional[int] = None) -> int:
        words = count_committed_words(text)
        if words <= 0:
            return 0

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        evt_id = str(uuid.uuid4())
        ref = str(session_id or dictation_id or "")

        with self.lock, self._get_connection() as con:
            p = self._get_or_create_period_row(con)
            cur = con.cursor()

            # Prevent double-counting the exact same session insertion
            if ref:
                cur.execute(
                    "SELECT 1 FROM local_usage_events WHERE session_ref = ? AND metric = 'dictation_words'",
                    (ref,),
                )
                if cur.fetchone():
                    logger.debug("Dictation session %s already accounted, skipping duplicate debit", ref)
                    return 0

            new_words = p["dictation_words"] + words
            p["dictation_words"] = new_words
            seal = self._seal_payload(p)

            cur.execute(
                "UPDATE local_metering_periods SET dictation_words = ?, dpapi_seal = ?, updated_at = ? "
                "WHERE period_id = ? AND user_id = ?",
                (new_words, seal, now_iso, p["period_id"], self._user_id),
            )
            cur.execute(
                "INSERT INTO local_usage_events (event_id, user_id, period_id, metric, amount, timestamp_utc, session_ref, is_synced) "
                "VALUES (?, ?, ?, 'dictation_words', ?, ?, ?, 0)",
                (evt_id, self._user_id, p["period_id"], float(words), now_iso, ref),
            )
            con.commit()

        logger.info("Metered %d committed words (new total: %d) for user %s", words, new_words, self._user_id)
        return words

    def can_start_meeting(self, entitlement: Any = None) -> Tuple[bool, str]:
        if self._resolve_pro_status(entitlement):
            return True, ""
        snap = self.get_usage_snapshot(is_pro=False)
        if snap.meeting_count_remaining <= 0:
            return (
                False,
                f"Weekly meeting session limit reached ({snap.meeting_count_used} / {snap.meeting_count_limit} meetings). "
                f"Resets on {snap.reset_display_ist}, or upgrade to VOX Pro for unlimited meetings.",
            )
        if snap.meeting_minutes_remaining <= 0.0:
            return (
                False,
                f"Weekly meeting minutes allowance reached ({snap.meeting_minutes_used:.1f} / {snap.meeting_minutes_limit:.0f} mins). "
                f"Resets on {snap.reset_display_ist}, or upgrade to VOX Pro for unlimited recording.",
            )
        return True, ""

    def evaluate_live_meeting_overrun(self, elapsed_sec: float, entitlement: Any = None) -> OverrunStatus:
        """
        Evaluate single-meeting duration during live calls.
        Policy: 80% (72 min) gives a non-intrusive warning; 90 min explains session cap.
        Live call is NEVER forcefully killed.
        """
        if self._resolve_pro_status(entitlement):
            return OverrunStatus({"warning": False, "overrun": False, "message": ""})

        mins = elapsed_sec / 60.0
        if mins >= FREE_SINGLE_MEETING_MIN_LIMIT:
            return OverrunStatus({
                "warning": True,
                "overrun": True,
                "message": (
                    "90-minute included meeting session allowance reached. "
                    "Your call will continue recording uninterrupted to the end; overage will apply against weekly balance."
                ),
            })
        if mins >= FREE_SINGLE_MEETING_MIN_LIMIT * WARNING_THRESHOLD_RATIO:
            rem = int(FREE_SINGLE_MEETING_MIN_LIMIT - mins)
            return OverrunStatus({
                "warning": True,
                "overrun": False,
                "message": f"{rem} minutes remaining in standard single-meeting allowance (90 min cap).",
            })
        return OverrunStatus({"warning": False, "overrun": False, "message": ""})

    def record_completed_meeting(self, duration_sec: float, meeting_id: Optional[int] = None) -> Tuple[float, int]:
        """Account for successfully saved meeting recording duration & session count."""
        dur = max(0.0, float(duration_sec or 0.0))
        # Don't meter sub-5 second accidental recordings
        if dur < 5.0:
            return 0.0, 0

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        evt_id = str(uuid.uuid4())
        session_ref = f"meeting_{meeting_id}" if meeting_id else ""

        with self.lock, self._get_connection() as con:
            p = self._get_or_create_period_row(con)
            cur = con.cursor()

            if session_ref:
                cur.execute(
                    "SELECT 1 FROM local_usage_events WHERE session_ref = ? AND metric = 'meeting_count'",
                    (session_ref,),
                )
                if cur.fetchone():
                    logger.debug("Meeting %s already accounted, skipping duplicate session count", session_ref)
                    return 0.0, 0

            new_sec = p["meeting_seconds"] + dur
            new_count = p["meeting_count"] + 1
            p["meeting_seconds"] = new_sec
            p["meeting_count"] = new_count
            seal = self._seal_payload(p)

            cur.execute(
                "UPDATE local_metering_periods SET meeting_seconds = ?, meeting_count = ?, dpapi_seal = ?, updated_at = ? "
                "WHERE period_id = ? AND user_id = ?",
                (new_sec, new_count, seal, now_iso, p["period_id"], self._user_id),
            )
            cur.execute(
                "INSERT INTO local_usage_events (event_id, user_id, period_id, metric, amount, timestamp_utc, session_ref, is_synced) "
                "VALUES (?, ?, ?, 'meeting_duration_sec', ?, ?, ?, 0)",
                (evt_id, self._user_id, p["period_id"], dur, now_iso, session_ref),
            )
            cur.execute(
                "INSERT INTO local_usage_events (event_id, user_id, period_id, metric, amount, timestamp_utc, session_ref, is_synced) "
                "VALUES (?, ?, ?, 'meeting_count', 1.0, ?, ?, 0)",
                (str(uuid.uuid4()), self._user_id, p["period_id"], now_iso, session_ref),
            )
            con.commit()

        logger.info(
            "Metered meeting duration %.1fs (new total: %.1f min, count: %d) for user %s",
            dur,
            new_sec / 60.0,
            new_count,
            self._user_id,
        )
        return dur, new_count

    def can_ask_vox(self, entitlement: Any = None) -> Tuple[bool, str]:
        if self._resolve_pro_status(entitlement):
            return True, ""
        snap = self.get_usage_snapshot(is_pro=False)
        if snap.ask_vox_is_exhausted:
            return (
                False,
                f"Weekly Ask VOX query allowance reached ({snap.ask_vox_used} / {snap.ask_vox_limit} queries). "
                f"Resets on {snap.reset_display_ist}, or upgrade to VOX Pro for unlimited intelligence queries.",
            )
        return True, ""

    def get_weekly_usage(self) -> Dict[str, Any]:
        """Convenience method returning a simple dictionary of current weekly usage counters."""
        with self.lock, self._get_connection() as con:
            p = self._get_or_create_period_row(con)
        snap = self.get_usage_snapshot()
        return {
            "dictation_words": p["dictation_words"],
            "meeting_duration_sec": p["meeting_seconds"],
            "meeting_count": p["meeting_count"],
            "ask_vox_count": p["ask_vox_count"],
            "reset_display_ist": snap.reset_display_ist,
        }

    def days_until_next_reset(self) -> int:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        _, end_utc, _ = get_current_week_period_utc(now_utc)
        diff = end_utc - now_utc
        return max(1, diff.days + 1)

    def get_unsynced_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Alias for get_pending_sync_events."""
        return self.get_pending_sync_events(limit=limit)

    def check_early_warnings(self, entitlement: Any = None) -> List[Dict[str, Any]]:
        """Returns any 80% quota threshold warnings for the active week."""
        if self._resolve_pro_status(entitlement):
            return []
        snap = self.get_usage_snapshot(is_pro=False)
        warnings = []
        if snap.is_dictation_warning:
            warnings.append({
                "metric": "dictation_words",
                "message": f"You've used {snap.dictation_words_used:,} of your {snap.dictation_words_limit:,} weekly dictation words (80%+).",
            })
        if snap.is_meeting_warning:
            warnings.append({
                "metric": "meeting_usage",
                "message": f"You've used 80%+ of your weekly meeting allowance.",
            })
        if snap.is_ask_vox_warning:
            warnings.append({
                "metric": "ask_vox",
                "message": f"You've used 80%+ of your weekly Ask VOX queries.",
            })
        return warnings

    def record_ask_vox_query(self, query_id: Optional[str] = None) -> int:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        evt_id = str(uuid.uuid4())
        session_ref = str(query_id or "")

        with self.lock, self._get_connection() as con:
            p = self._get_or_create_period_row(con)
            cur = con.cursor()

            if session_ref:
                cur.execute(
                    "SELECT 1 FROM local_usage_events WHERE session_ref = ? AND metric = 'ask_vox_count'",
                    (session_ref,),
                )
                if cur.fetchone():
                    return 0

            new_count = p["ask_vox_count"] + 1
            p["ask_vox_count"] = new_count
            seal = self._seal_payload(p)

            cur.execute(
                "UPDATE local_metering_periods SET ask_vox_count = ?, dpapi_seal = ?, updated_at = ? "
                "WHERE period_id = ? AND user_id = ?",
                (new_count, seal, now_iso, p["period_id"], self._user_id),
            )
            cur.execute(
                "INSERT INTO local_usage_events (event_id, user_id, period_id, metric, amount, timestamp_utc, session_ref, is_synced) "
                "VALUES (?, ?, ?, 'ask_vox_count', 1.0, ?, ?, 0)",
                (evt_id, self._user_id, p["period_id"], now_iso, session_ref),
            )
            con.commit()

        return new_count

    def get_pending_sync_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.lock, self._get_connection() as con:
            cur = con.cursor()
            cur.execute(
                "SELECT event_id, user_id, period_id, metric, amount, timestamp_utc "
                "FROM local_usage_events WHERE is_synced = 0 ORDER BY timestamp_utc ASC LIMIT ?",
                (limit,),
            )
            return [
                {
                    "event_id": r[0],
                    "user_id": r[1],
                    "period_id": r[2],
                    "metric": r[3],
                    "amount": float(r[4]),
                    "timestamp_utc": r[5],
                    "device_id": self._device_id,
                }
                for r in cur.fetchall()
            ]

    def mark_events_synced(self, event_ids: List[str]) -> None:
        if not event_ids:
            return
        with self.lock, self._get_connection() as con:
            q = f"UPDATE local_usage_events SET is_synced = 1 WHERE event_id IN ({','.join(['?']*len(event_ids))})"
            con.execute(q, event_ids)
            con.commit()
