"""
VoxLocal Unified Calendar Engine & Multi-Account Synchronizer.
Implements Feature Contract:
- Multi-account & multi-provider aggregation across Microsoft (Personal Outlook, M365, Entra ID),
  Google (Gmail, Google Workspace), Zoom, ICS, and Outlook MAPI
- Fault-isolated synchronization per account with exponential backoff & HTTP 429 Retry-After respect
- Incremental synchronization via MS Graph Delta Queries & Google Calendar syncToken with DB persistence
- Offline & restart resilience via SQLite event cache hydration (`calendar_events` + `calendar_accounts`)
- High-precision time-windowed deduplication (never collapsing recurring series occurrences)
- Qt Signals for CalendarPage, Notetaker strip, 1-click Join/Prepare, and acoustic priming
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from voxlocal.calendar.auth_service import classify_account_kind, make_account_id
from voxlocal.calendar.base import (
    AccountConnectionState,
    BaseCalendarProvider,
    CalendarMeeting,
    CalendarProviderType,
)
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

    cleaned = re.sub(r"^https?://", "", u).split("?")[0].rstrip("/")
    return cleaned[:80] if len(cleaned) > 10 else ""


def normalize_title(title: str) -> str:
    """Normalize title for fuzzy deduplication."""
    clean = re.sub(r"[^\w\s]", "", (title or "").lower())
    words = [w for w in clean.split() if w not in ("meeting", "sync", "call", "catchup", "discussion", "re")]
    return " ".join(words).strip()


def _to_utc_timestamp(dt: datetime) -> float:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).timestamp()
    return dt.timestamp()


class UnifiedCalendarEngine(QObject):
    """
    Central Multi-Provider & Multi-Account Calendar Engine for VOX.
    Coordinates fault-isolated synchronization, incremental delta caching, and cross-account deduplication.
    """

    upcoming_meetings_updated = pyqtSignal(list)       # List[CalendarMeeting]
    providers_status_changed = pyqtSignal(dict)        # Dict[str, Dict[str, Any]]
    accounts_updated = pyqtSignal(list)                # List[Dict[str, Any]]
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
        db: Any = None,
        user_id: str = "default",
        auth_broker: Any = None,
        database: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(parent)
        self._providers: Dict[str, BaseCalendarProvider] = dict(providers or {})
        self._account_enabled: Dict[str, bool] = {k: True for k in self._providers}
        self._account_meta: Dict[str, Dict[str, Any]] = {}
        self._account_failures: Dict[str, int] = {}
        self._account_backoff_until: Dict[str, float] = {}
        self._last_sync_at: Optional[str] = None
        self._evaluator = CalendarAlertEvaluator()
        self._cached_meetings: List[CalendarMeeting] = []
        self._lock = threading.Lock()
        self._is_syncing: bool = False
        self._db = db if db is not None else database
        self.user_id = (user_id or "default").strip() or "default"
        self.auth_broker = auth_broker

        self._timer = QTimer(self)
        self._timer.setInterval(max(10, poll_interval_sec) * 1000)
        self._timer.timeout.connect(self.poll_all_now)

        if self._db is not None:
            self.hydrate_from_database()

    def set_database(self, db: Any, user_id: str = "default") -> None:
        self._db = db
        self.user_id = (user_id or "default").strip() or "default"
        self.hydrate_from_database()

    def hydrate_from_database(self) -> List[CalendarMeeting]:
        """
        Load cached synchronized events and account metadata from SQLite on startup / user switch
        so VOX displays schedules immediately on offline start or before first network sync.
        """
        if self._db is None:
            return []
        try:
            accounts = self._db.list_calendar_accounts(user_id=self.user_id)
            enabled_accounts: set[str] = set()
            with self._lock:
                for acc in accounts:
                    acc_id = acc["account_id"]
                    self._account_meta[acc_id] = acc
                    if acc_id not in self._account_enabled:
                        self._account_enabled[acc_id] = bool(acc.get("enabled", True))
                    if self._account_enabled[acc_id]:
                        enabled_accounts.add(acc_id)
                    if acc.get("last_sync_at") and (not self._last_sync_at or acc["last_sync_at"] > self._last_sync_at):
                        self._last_sync_at = acc["last_sync_at"]

            now_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            raw_rows = self._db.list_calendar_events(
                user_id=self.user_id,
                start_iso=now_iso,
                include_cancelled=False,
            )
            hydrated: List[CalendarMeeting] = []
            for row in raw_rows:
                acc_id = str(row.get("account_id") or "")
                if enabled_accounts and acc_id and acc_id not in enabled_accounts:
                    continue
                try:
                    hydrated.append(CalendarMeeting.from_dict(row))
                except Exception:
                    continue

            deduped = self._deduplicate_meetings(hydrated)
            deduped.sort(key=lambda m: _to_utc_timestamp(m.start_time))
            with self._lock:
                self._cached_meetings = deduped
            if deduped:
                self.upcoming_meetings_updated.emit(deduped)
            self.accounts_updated.emit(self.list_accounts())
            return deduped
        except Exception as exc:
            logger.warning("Failed hydrating calendar cache from DB: %s", exc)
            return []

    @property
    def providers(self) -> Dict[str, BaseCalendarProvider]:
        with self._lock:
            return dict(self._providers)

    @staticmethod
    def _infer_provider_type(key: str, prov: BaseCalendarProvider) -> str:
        try:
            st = prov.get_provider_status()
            if isinstance(st, dict) and st.get("provider_type"):
                return str(st["provider_type"])
        except Exception:
            pass
        if ":" in key:
            return key.split(":", 1)[0]
        return key

    def register_provider(
        self,
        key: str,
        provider: BaseCalendarProvider,
        sync_immediately: bool = True,
        account_meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Register or update an active calendar provider (account_id or legacy provider key)."""
        with self._lock:
            self._providers[key] = provider
            self._account_enabled.setdefault(key, True)
            if account_meta:
                self._account_meta[key] = dict(account_meta)
            self._account_failures[key] = 0
            self._account_backoff_until[key] = 0.0

        self._persist_provider_account_record(key, provider)
        logger.info("Registered calendar provider '%s'", key)
        self.emit_providers_status()
        self.accounts_updated.emit(self.list_accounts())
        if sync_immediately:
            self.poll_all_now(force=True)
        return key

    def register_account_provider(
        self,
        account_id: str,
        provider: BaseCalendarProvider,
        account_meta: Optional[Dict[str, Any]] = None,
        sync_immediately: bool = True,
    ) -> str:
        return self.register_provider(
            account_id,
            provider,
            sync_immediately=sync_immediately,
            account_meta=account_meta,
        )

    def unregister_provider(self, key: str) -> bool:
        """Remove and disconnect a calendar provider (exact key or legacy provider-type wildcard)."""
        removed_keys: List[Tuple[str, BaseCalendarProvider]] = []
        with self._lock:
            if key in self._providers:
                prov = self._providers.pop(key)
                removed_keys.append((key, prov))
            else:
                # Legacy key compatibility: unregister all accounts matching provider type prefix
                matching = [
                    k for k, p in self._providers.items()
                    if k == key or k.startswith(f"{key}:") or self._infer_provider_type(k, p) == key
                ]
                for mk in matching:
                    removed_keys.append((mk, self._providers.pop(mk)))
            for rk, _ in removed_keys:
                self._account_enabled.pop(rk, None)
                self._account_meta.pop(rk, None)
                self._account_failures.pop(rk, None)
                self._account_backoff_until.pop(rk, None)

        if not removed_keys:
            return False

        for rk, prov in removed_keys:
            try:
                prov.disconnect()
            except Exception as exc:
                logger.debug("Provider disconnect note for %s: %s", rk, exc)
            if self._db is not None:
                try:
                    self._db.delete_calendar_account(rk, user_id=self.user_id)
                except Exception as exc:
                    logger.warning("Failed removing DB calendar account %s: %s", rk, exc)
            logger.info("Unregistered calendar provider '%s'", rk)

        self._rebuild_cache_from_db_or_memory()
        self.emit_providers_status()
        self.accounts_updated.emit(self.list_accounts())
        self.poll_all_now(force=True)
        return True

    def get_provider(self, key: str) -> Optional[BaseCalendarProvider]:
        with self._lock:
            if key in self._providers:
                return self._providers[key]
            # Legacy lookup: e.g. get_provider("ms_graph") finds first active ms_graph:* account
            candidates = [
                prov for k, prov in self._providers.items()
                if k.startswith(f"{key}:") or self._infer_provider_type(k, prov) == key
            ]
        for prov in candidates:
            try:
                if prov.is_authenticated():
                    return prov
            except Exception:
                pass
        return candidates[0] if candidates else None

    def set_account_enabled(self, account_id: str, enabled: bool) -> bool:
        with self._lock:
            exists = account_id in self._providers or account_id in self._account_meta
            if not exists:
                return False
            self._account_enabled[account_id] = bool(enabled)
            if account_id in self._account_meta:
                self._account_meta[account_id]["enabled"] = bool(enabled)
        if self._db is not None:
            try:
                self._db.set_calendar_account_enabled(account_id, bool(enabled), user_id=self.user_id)
            except Exception as exc:
                logger.warning("Failed updating account enabled state in DB: %s", exc)
        if enabled:
            self._account_backoff_until[account_id] = 0.0
            self.sync_account_now(account_id)
        else:
            self._rebuild_cache_from_db_or_memory()
        self.emit_providers_status()
        self.accounts_updated.emit(self.list_accounts())
        return True

    def set_account_selected_calendars(self, account_id: str, calendar_ids: List[str]) -> bool:
        prov = self.get_provider(account_id)
        if prov is None:
            return False
        prov.set_selected_calendars(calendar_ids)
        self._persist_provider_account_record(account_id, prov)
        if self._db is not None:
            try:
                self._db.clear_calendar_events_for_account(account_id, user_id=self.user_id)
            except Exception:
                pass
        self.sync_account_now(account_id, use_incremental=False)
        self.accounts_updated.emit(self.list_accounts())
        return True

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

    def _build_account_snapshot(self, key: str, prov: Optional[BaseCalendarProvider]) -> Dict[str, Any]:
        db_meta = dict(self._account_meta.get(key) or {})
        status = {}
        is_auth = False
        if prov is not None:
            try:
                status = dict(prov.get_provider_status() or {})
            except Exception as exc:
                status = {"provider_type": db_meta.get("provider_type", "unknown"), "is_healthy": False, "error_state": str(exc)}
            try:
                # Check token state cheaply without triggering interactive prompts
                is_auth = bool(getattr(prov, "_access_token", None) or getattr(prov, "_feed_url", None) or getattr(prov, "_authenticated", False) or prov.is_authenticated())
            except Exception:
                is_auth = False

        ptype = str(status.get("provider_type") or db_meta.get("provider_type") or (key.split(":", 1)[0] if ":" in key else key))
        email = str(
            status.get("account_upn")
            or status.get("account_email")
            or db_meta.get("account_email")
            or ""
        )
        display_name = str(status.get("display_name") or db_meta.get("display_name") or email or key)
        tenant_id = str(status.get("tenant_id") or db_meta.get("tenant_id") or "")
        hosted_dom = str(status.get("hosted_domain") or "")
        acct_kind = str(
            status.get("account_kind")
            or db_meta.get("account_kind")
            or classify_account_kind(ptype, email=email, tenant_id=tenant_id, hosted_domain=hosted_dom)
        )
        enabled = bool(self._account_enabled.get(key, db_meta.get("enabled", True)))
        err_state = status.get("error_state") or db_meta.get("error_code")
        conn_state = status.get("connection_state") or db_meta.get("connection_state")
        if not enabled:
            conn_state = "disabled"
        elif err_state in ("AUTH_REQUIRED", "AUTH_EXPIRED", "REFRESH_FAILED", "NO_REFRESH_TOKEN"):
            conn_state = AccountConnectionState.EXPIRED.value
        elif err_state == "ADMIN_APPROVAL_REQUIRED":
            conn_state = AccountConnectionState.ADMIN_APPROVAL_REQUIRED.value
        elif err_state == "DISCONNECTED" or (prov is not None and not is_auth and not err_state):
            conn_state = AccountConnectionState.DISCONNECTED.value
        elif err_state == "NETWORK_OFFLINE":
            conn_state = "offline_cached" if is_auth else AccountConnectionState.DISCONNECTED.value
        elif err_state or status.get("is_healthy") is False:
            conn_state = AccountConnectionState.FAILED.value
        elif is_auth:
            conn_state = AccountConnectionState.CONNECTED.value

        return {
            "account_id": key,
            "user_id": self.user_id,
            "provider_type": ptype,
            "account_email": email,
            "display_name": display_name,
            "account_kind": acct_kind,
            "tenant_id": tenant_id,
            "enabled": enabled,
            "is_authenticated": is_auth,
            "is_healthy": bool(status.get("is_healthy", True)) and not err_state,
            "connection_state": conn_state or AccountConnectionState.DISCONNECTED.value,
            "last_sync_at": status.get("last_sync_at") or status.get("last_poll_time") or db_meta.get("last_sync_at") or "",
            "last_error": status.get("friendly_error") or status.get("error_detail") or db_meta.get("last_error") or "",
            "error_code": err_state or "",
            "selected_calendars": list(status.get("selected_calendars") or db_meta.get("selected_calendars") or []),
            "available_calendars": list(status.get("available_calendars") or db_meta.get("available_calendars") or []),
        }

    def list_accounts(self) -> List[Dict[str, Any]]:
        with self._lock:
            keys = list(dict.fromkeys(list(self._providers.keys()) + list(self._account_meta.keys())))
            prov_map = dict(self._providers)
        return [self._build_account_snapshot(k, prov_map.get(k)) for k in keys]

    def get_sync_health_summary(self) -> Dict[str, Any]:
        accounts = self.list_accounts()
        active = [a for a in accounts if a.get("enabled", True)]
        healthy = [a for a in active if a.get("connection_state") in ("connected", "syncing", "offline_cached")]
        degraded = [a for a in active if a.get("connection_state") in ("expired", "failed", "admin_approval_required")]
        sync_times = [str(a.get("last_sync_at") or "") for a in accounts if a.get("last_sync_at")]
        latest_sync = max(sync_times) if sync_times else (self._last_sync_at or "")

        if not active:
            state = "not_connected"
        elif degraded and not healthy:
            state = "attention_needed"
        elif degraded:
            state = "partial"
        elif self._is_syncing:
            state = "syncing"
        else:
            state = "healthy"

        return {
            "state": state,
            "connected_accounts": len(active),
            "total_accounts": len(accounts),
            "healthy_accounts": len(healthy),
            "degraded_accounts": len(degraded),
            "last_sync_at": latest_sync,
            "errors": [
                {"account_id": a["account_id"], "error": a["last_error"], "code": a["error_code"]}
                for a in degraded
                if a.get("last_error")
            ],
        }

    def emit_providers_status(self) -> None:
        """Emit composite status dictionary for all registered providers."""
        status_map: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            items = list(self._providers.items())
        for k, prov in items:
            try:
                status_map[k] = prov.get_provider_status()
            except Exception as exc:
                status_map[k] = {"provider_type": k, "is_healthy": False, "error_state": str(exc)}
        self.providers_status_changed.emit(status_map)

    def _persist_provider_account_record(self, key: str, prov: BaseCalendarProvider) -> None:
        if self._db is None:
            return
        try:
            snap = self._build_account_snapshot(key, prov)
            delta_links = dict(getattr(prov, "_delta_links", {}) or {})
            sync_tokens = dict(getattr(prov, "_sync_tokens", {}) or {})
            self._db.upsert_calendar_account(
                account_id=key,
                provider_type=snap["provider_type"],
                user_id=self.user_id,
                account_email=snap["account_email"],
                display_name=snap["display_name"],
                account_kind=snap["account_kind"],
                tenant_id=snap["tenant_id"],
                enabled=snap["enabled"],
                connection_state=snap["connection_state"],
                last_sync_at=snap["last_sync_at"],
                last_error=snap["last_error"],
                error_code=snap["error_code"],
                selected_calendars=snap["selected_calendars"],
                available_calendars=snap["available_calendars"],
                delta_links=delta_links,
                sync_tokens=sync_tokens,
            )
            with self._lock:
                self._account_meta[key] = snap
        except Exception as exc:
            logger.debug("Failed saving account metadata for %s: %s", key, exc)

    def _sync_single_provider(
        self,
        key: str,
        prov: BaseCalendarProvider,
        use_incremental: bool = False,
        force: bool = False,
    ) -> List[CalendarMeeting]:
        """Synchronize one account safely with isolation, backoff, and DB cache fallback."""
        now_mono = time.monotonic()
        if not force and now_mono < self._account_backoff_until.get(key, 0.0):
            return self._load_cached_account_meetings(key)

        try:
            if not prov.is_authenticated():
                self._persist_provider_account_record(key, prov)
                return self._load_cached_account_meetings(key)
        except Exception as exc:
            logger.warning("Auth check failed for '%s': %s", key, exc)
            return self._load_cached_account_meetings(key)

        ptype = self._infer_provider_type(key, prov)
        items: List[CalendarMeeting] = []
        removed_ids: List[str] = []
        is_incremental = False
        sync_ok = True

        try:
            if use_incremental and hasattr(prov, "sync_events_delta"):
                res = prov.sync_events_delta(window_minutes=43200, use_delta=True)
                items = list(res.get("meetings") or [])
                removed_ids = list(res.get("removed_ids") or [])
                is_incremental = bool(res.get("is_incremental", False))
                sync_ok = bool(res.get("ok", True))
            elif use_incremental and hasattr(prov, "sync_events_incremental"):
                res = prov.sync_events_incremental(window_minutes=43200, use_sync_token=True)
                items = list(res.get("meetings") or [])
                removed_ids = list(res.get("removed_ids") or [])
                is_incremental = bool(res.get("is_incremental", False))
                sync_ok = bool(res.get("ok", True))
            else:
                items = list(prov.get_upcoming_meetings(window_minutes=43200) or [])
                st = prov.get_provider_status() if hasattr(prov, "get_provider_status") else {}
                sync_ok = bool(st.get("is_healthy", True))
        except Exception as exc:
            logger.error("Error polling calendar provider '%s': %s", key, exc)
            sync_ok = False

        status = {}
        try:
            status = dict(prov.get_provider_status() or {})
        except Exception:
            status = {}

        if not sync_ok or status.get("is_healthy") is False:
            failures = self._account_failures.get(key, 0) + 1
            self._account_failures[key] = failures
            retry_after = int(getattr(prov, "retry_after_seconds", 0) or 0)
            is_offline = (status.get("error_state") == "NETWORK_OFFLINE")
            if is_offline:
                backoff_sec = 25  # short retry window for transient network recovery
            elif retry_after > 0:
                backoff_sec = retry_after
            else:
                backoff_sec = min(900, 30 * (2 ** min(failures - 1, 5)))
            self._account_backoff_until[key] = time.monotonic() + float(backoff_sec)
            self._persist_provider_account_record(key, prov)
            # Preserve cached events offline/on rate-limit instead of vanishing schedule
            cached = self._load_cached_account_meetings(key)
            return items or cached

        self._account_failures[key] = 0
        self._account_backoff_until[key] = 0.0
        account_email = str(status.get("account_upn") or status.get("account_email") or "")
        for it in items:
            if not it.provider_type:
                it.provider_type = ptype
            if not it.account_id:
                it.account_id = key
            if account_email and not it.provider_account:
                it.provider_account = account_email
            if it.account_id and it.account_id not in it.source_accounts:
                it.source_accounts.append(it.account_id)
            if it.provider_type and it.provider_type not in it.source_providers:
                it.source_providers.append(it.provider_type)

        if self._db is not None:
            try:
                if is_incremental:
                    if removed_ids:
                        self._db.delete_calendar_events_by_ids(removed_ids, user_id=self.user_id, account_id=key)
                    if items:
                        self._db.upsert_calendar_events(items, user_id=self.user_id, account_id=key)
                    # Return full active set for this account from DB so cache has unchanged + delta updates
                    items = self._load_cached_account_meetings(key)
                else:
                    self._db.clear_calendar_events_for_account(key, user_id=self.user_id)
                    if items:
                        self._db.upsert_calendar_events(items, user_id=self.user_id, account_id=key)
            except Exception as exc:
                logger.warning("Failed persisting calendar events to DB for %s: %s", key, exc)

        self._persist_provider_account_record(key, prov)
        return items

    def _load_cached_account_meetings(self, account_id: str) -> List[CalendarMeeting]:
        if self._db is None:
            with self._lock:
                return [m for m in self._cached_meetings if m.account_id == account_id or account_id in m.source_accounts]
        try:
            now_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            rows = self._db.list_calendar_events(
                user_id=self.user_id,
                account_id=account_id,
                start_iso=now_iso,
                include_cancelled=False,
            )
            return [CalendarMeeting.from_dict(r) for r in rows]
        except Exception:
            return []

    def _rebuild_cache_from_db_or_memory(self) -> None:
        if self._db is not None:
            self.hydrate_from_database()
            return
        with self._lock:
            enabled_keys = {k for k, en in self._account_enabled.items() if en}
            kept = [m for m in self._cached_meetings if not m.account_id or m.account_id in enabled_keys]
            self._cached_meetings = kept
        self.upcoming_meetings_updated.emit(kept)

    def sync_account_now(self, account_id: str, use_incremental: bool = True) -> List[CalendarMeeting]:
        """Synchronize one specific connected account without affecting other accounts."""
        prov = self.get_provider(account_id)
        if prov is None:
            return self.get_upcoming_meetings()
        if not self._account_enabled.get(account_id, True):
            return self.get_upcoming_meetings()

        self.sync_started.emit()
        synced_for_acc = self._sync_single_provider(account_id, prov, use_incremental=use_incremental, force=True)

        # Combine with other active accounts cleanly
        with self._lock:
            other_items = [
                m for m in self._cached_meetings
                if m.account_id != account_id and account_id not in (m.source_accounts or [])
            ]
        deduped = self._deduplicate_meetings(other_items + synced_for_acc)
        deduped.sort(key=lambda m: _to_utc_timestamp(m.start_time))

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._cached_meetings = deduped
            self._last_sync_at = now_iso

        self.upcoming_meetings_updated.emit(deduped)
        self.emit_providers_status()
        self.accounts_updated.emit(self.list_accounts())
        self.sync_finished.emit(len(deduped))
        return deduped

    def poll_all_sync(self, force: bool = False, use_incremental: bool = False) -> List[CalendarMeeting]:
        """Execute synchronous multi-account synchronization and return deduplicated meetings."""
        with self._lock:
            if self._is_syncing:
                return list(self._cached_meetings)
            self._is_syncing = True
            providers_snapshot = dict(self._providers)
            enabled_snapshot = dict(self._account_enabled)

        self.sync_started.emit()
        raw_meetings: List[CalendarMeeting] = []
        try:
            for key, prov in providers_snapshot.items():
                if not enabled_snapshot.get(key, True):
                    continue
                raw_meetings.extend(
                    self._sync_single_provider(key, prov, use_incremental=use_incremental, force=force)
                )

            deduped = self._deduplicate_meetings(raw_meetings)
            deduped.sort(key=lambda m: _to_utc_timestamp(m.start_time))

            now_iso = datetime.now(timezone.utc).isoformat()
            with self._lock:
                self._cached_meetings = deduped
                self._last_sync_at = now_iso
                self._is_syncing = False

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
            self.accounts_updated.emit(self.list_accounts())
            self.sync_finished.emit(len(deduped))
            return deduped
        except Exception as exc:
            logger.error("Unexpected calendar sync failure: %s", exc)
            with self._lock:
                self._is_syncing = False
                cached = list(self._cached_meetings)
            self.emit_providers_status()
            self.sync_finished.emit(len(cached))
            return cached

    def poll_all_now(self, force: bool = False, use_incremental: bool = False) -> List[CalendarMeeting]:
        """
        Execute an immediate background synchronization across all enabled providers.
        Thread-safe: spawns worker thread so the UI thread never blocks.
        """
        with self._lock:
            if self._is_syncing:
                return list(self._cached_meetings)

        threading.Thread(
            target=self.poll_all_sync,
            args=(force, use_incremental),
            daemon=True,
            name="vox-calendar-sync",
        ).start()
        return self.get_upcoming_meetings()

    def _deduplicate_meetings(self, meetings: List[CalendarMeeting]) -> List[CalendarMeeting]:
        """
        Deduplicate meetings appearing in multiple connected calendars.
        Matches by:
        0. Identical iCalUId within +/- 15 minutes
        1. Matching normalized meeting join URL / conference ID within +/- 30 minutes
           (so recurring series sharing one URL across days/weeks NEVER collapse into 1 entry!)
        2. Same start time (+/- 5 min) and matching normalized title
        Merges attendees, preserves source accounts & event IDs, keeps best video link and platform.
        """
        merged_list: List[CalendarMeeting] = []

        for item in meetings:
            if item.is_cancelled:
                continue

            join_id = normalize_meeting_join_id(item.join_url)
            norm_t = normalize_title(item.title)
            st_ts = _to_utc_timestamp(item.start_time)

            matched_idx: Optional[int] = None
            for idx, existing in enumerate(merged_list):
                ex_ts = _to_utc_timestamp(existing.start_time)

                # Check 0: Exact iCalUID within 15 min
                if item.ical_uid and existing.ical_uid and item.ical_uid == existing.ical_uid and abs(st_ts - ex_ts) <= 900:
                    matched_idx = idx
                    break

                # Check 1: Join ID match within 30 min window
                ex_join_id = normalize_meeting_join_id(existing.join_url)
                if join_id and ex_join_id and join_id == ex_join_id and abs(st_ts - ex_ts) <= 1800:
                    matched_idx = idx
                    break

                # Check 2: Time window (+/- 5 min) and Title match
                if abs(st_ts - ex_ts) <= 300:
                    ex_norm_t = normalize_title(existing.title)
                    if norm_t and ex_norm_t and (norm_t == ex_norm_t or norm_t in ex_norm_t or ex_norm_t in norm_t):
                        matched_idx = idx
                        break

            if matched_idx is not None:
                target = merged_list[matched_idx]
                combined_attendees = list(target.attendees)
                for att in item.attendees:
                    if att and att not in combined_attendees:
                        combined_attendees.append(att)
                target.attendees = combined_attendees

                if not target.join_url and item.join_url:
                    target.join_url = item.join_url
                    target.platform = item.platform
                    target.is_online = True
                elif target.platform in ("generic", "in_person", "") and item.platform not in ("generic", "in_person", ""):
                    target.platform = item.platform

                if item.id and item.id not in target.source_event_ids:
                    target.source_event_ids.append(item.id)
                if item.account_id and item.account_id not in target.source_accounts:
                    target.source_accounts.append(item.account_id)
                elif item.provider_account and item.provider_account not in target.source_accounts:
                    target.source_accounts.append(item.provider_account)
                if item.provider_type and item.provider_type not in target.source_providers:
                    target.source_providers.append(item.provider_type)

                if item.provider_type and item.provider_type not in target.provider_type:
                    target.provider_type = f"{target.provider_type}+{item.provider_type}"
            else:
                merged_list.append(item)

        return merged_list

    def get_upcoming_meetings(self, limit: Optional[int] = None, *args: Any, **kwargs: Any) -> List[CalendarMeeting]:
        """Return cached synchronized meetings, optionally sliced by limit."""
        with self._lock:
            meetings = list(self._cached_meetings)
            if limit is not None and limit > 0:
                return meetings[:limit]
            return meetings

    def get_today_meetings(self) -> List[CalendarMeeting]:
        """Return meetings scheduled for today in local timezone."""
        return [m for m in self.get_upcoming_meetings() if m.is_today]

    def get_tomorrow_meetings(self) -> List[CalendarMeeting]:
        """Return meetings scheduled for tomorrow in local timezone."""
        return [m for m in self.get_upcoming_meetings() if m.is_tomorrow]

    def get_this_week_meetings(self) -> List[CalendarMeeting]:
        """Return meetings scheduled within the next 7 local calendar days (including today)."""
        return [m for m in self.get_upcoming_meetings() if m.is_this_week]

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
        k = key or getattr(provider, "_account_id", None) or getattr(provider, "provider_type", None) or "default"
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
