"""
Centralized Background Task Manager for VOX.

Provides:
- Unique task IDs & lifecycle states (QUEUED -> RUNNING -> COMPLETED / FAILED / CANCELLED / RETRYING)
- Deduplication by key so repeated clicks or navigations never spawn duplicate workers
- Separate bounded worker pools for general I/O tasks and serialized AI/embedding tasks
- Subprocess registration & clean cancellation/shutdown termination within bounded timeouts
"""

from __future__ import annotations

import enum
import inspect
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from voxlocal.core.logger import get_logger

logger = get_logger("core.task_manager")


class TaskState(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RETRYING = "RETRYING"


ACTIVE_STATES = {TaskState.QUEUED, TaskState.RUNNING, TaskState.RETRYING}


@dataclass
class ManagedTask:
    task_id: str
    task_type: str
    description: str = ""
    dedupe_key: Optional[str] = None
    lane: str = "io"
    state: TaskState = TaskState.QUEUED
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    progress_pct: float = 0.0
    progress_msg: str = ""
    error: str = ""
    timeout_sec: Optional[float] = None
    resource_owner: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    future: Optional[Future] = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "description": self.description,
            "dedupe_key": self.dedupe_key,
            "lane": self.lane,
            "state": self.state.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "progress_pct": self.progress_pct,
            "progress_msg": self.progress_msg,
            "error": self.error,
            "timeout_sec": self.timeout_sec,
            "resource_owner": self.resource_owner,
        }
        return d


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fn_accepts_cancel_event(fn: Callable[..., Any]) -> bool:
    try:
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())
        if any(p.name == "cancel_event" for p in params):
            return True
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
            return True
    except Exception:
        pass
    return False


class BackgroundTaskManager:
    """Centralized, bounded lifecycle manager for VOX background tasks and subprocesses."""

    def __init__(self, *, io_workers: int = 4, ai_workers: int = 1, max_history: int = 200) -> None:
        self._lock = threading.RLock()
        self._shutting_down = False
        self._io_pool = ThreadPoolExecutor(max_workers=max(1, io_workers), thread_name_prefix="VoxBgWorker")
        self._ai_pool = ThreadPoolExecutor(max_workers=max(1, ai_workers), thread_name_prefix="VoxAiWorker")
        self._tasks: Dict[str, ManagedTask] = {}
        self._task_order: List[str] = []
        self._max_history = max(20, int(max_history))
        self._subprocesses: Dict[str, Any] = {}

    def submit(
        self,
        task_type: str,
        fn: Callable[..., Any],
        *,
        description: str = "",
        dedupe_key: Optional[str] = None,
        lane: str = "io",
        timeout_sec: Optional[float] = None,
        resource_owner: str = "",
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> ManagedTask:
        with self._lock:
            if dedupe_key:
                for t in self._tasks.values():
                    if t.dedupe_key == dedupe_key and t.state in ACTIVE_STATES:
                        return t

            task_id = f"{task_type}-{uuid.uuid4().hex[:8]}"
            task = ManagedTask(
                task_id=task_id,
                task_type=task_type,
                description=description or task_type,
                dedupe_key=dedupe_key,
                lane="ai" if lane == "ai" else "io",
                state=TaskState.QUEUED,
                created_at=_utcnow_iso(),
                timeout_sec=timeout_sec,
                resource_owner=resource_owner,
            )

            if self._shutting_down:
                task.state = TaskState.CANCELLED
                task.completed_at = _utcnow_iso()
                task.cancel_event.set()
                self._record_task_locked(task)
                return task

            self._record_task_locked(task)
            pool = self._ai_pool if task.lane == "ai" else self._io_pool

        accepts_cancel = _fn_accepts_cancel_event(fn)

        def _runner() -> Any:
            if task.cancel_event.is_set() or self._shutting_down:
                with self._lock:
                    task.state = TaskState.CANCELLED
                    task.completed_at = _utcnow_iso()
                return None

            with self._lock:
                task.state = TaskState.RUNNING
                task.started_at = _utcnow_iso()

            try:
                res = fn(cancel_event=task.cancel_event) if accepts_cancel else fn()
                with self._lock:
                    if task.cancel_event.is_set():
                        task.state = TaskState.CANCELLED
                    else:
                        task.state = TaskState.COMPLETED
                        task.progress_pct = 100.0
                    task.completed_at = _utcnow_iso()
                if on_done is not None and not task.cancel_event.is_set():
                    try:
                        on_done(res)
                    except Exception as cb_exc:
                        logger.debug("Task %s on_done callback warning: %s", task.task_id, cb_exc)
                return res
            except Exception as exc:
                with self._lock:
                    task.state = TaskState.CANCELLED if task.cancel_event.is_set() else TaskState.FAILED
                    task.error = str(exc)
                    task.completed_at = _utcnow_iso()
                logger.error("Background task '%s' failed: %s", task.task_id, exc, exc_info=True)
                if on_error is not None:
                    try:
                        on_error(exc)
                    except Exception as cb_exc:
                        logger.debug("Task %s on_error callback warning: %s", task.task_id, cb_exc)
                return None
            finally:
                self.unregister_subprocess(task.task_id)

        try:
            task.future = pool.submit(_runner)
        except RuntimeError:
            with self._lock:
                task.state = TaskState.CANCELLED
                task.completed_at = _utcnow_iso()
        return task

    def _record_task_locked(self, task: ManagedTask) -> None:
        self._tasks[task.task_id] = task
        self._task_order.append(task.task_id)
        if len(self._task_order) > self._max_history:
            for tid in list(self._task_order):
                if len(self._task_order) <= self._max_history:
                    break
                cand = self._tasks.get(tid)
                if cand is None or cand.state not in ACTIVE_STATES:
                    self._task_order.remove(tid)
                    self._tasks.pop(tid, None)

    def update_progress(self, task_id: str, pct: float, msg: str = "") -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is not None:
                t.progress_pct = max(0.0, min(100.0, float(pct)))
                if msg:
                    t.progress_msg = str(msg)

    def register_subprocess(self, task_id: str, proc: Any) -> None:
        with self._lock:
            self._subprocesses[task_id] = proc

    def unregister_subprocess(self, task_id: str) -> None:
        with self._lock:
            self._subprocesses.pop(task_id, None)

    @staticmethod
    def _terminate_proc(proc: Any, wait_sec: float = 1.0) -> None:
        if proc is None:
            return
        try:
            if hasattr(proc, "poll") and proc.poll() is not None:
                return
            if hasattr(proc, "terminate"):
                proc.terminate()
            if hasattr(proc, "wait"):
                try:
                    proc.wait(timeout=wait_sec)
                    return
                except Exception:
                    pass
            if hasattr(proc, "kill"):
                proc.kill()
        except Exception:
            pass

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return False
            t.cancel_event.set()
            if t.state == TaskState.QUEUED:
                t.state = TaskState.CANCELLED
                t.completed_at = _utcnow_iso()
                if t.future is not None:
                    t.future.cancel()
            proc = self._subprocesses.pop(task_id, None)
        self._terminate_proc(proc)
        return True

    def cancel_by_type(self, task_type: str) -> int:
        with self._lock:
            ids = [tid for tid, t in self._tasks.items() if t.task_type == task_type and t.state in ACTIVE_STATES]
        for tid in ids:
            self.cancel(tid)
        return len(ids)

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            t = self._tasks.get(task_id)
            return t.to_dict() if t is not None else None

    def list_tasks(self, *, active_only: bool = False) -> List[Dict[str, Any]]:
        with self._lock:
            items = [self._tasks[tid] for tid in self._task_order if tid in self._tasks]
            if active_only:
                items = [t for t in items if t.state in ACTIVE_STATES]
            return [t.to_dict() for t in items]

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._tasks.values() if t.state in ACTIVE_STATES)

    def shutdown(self, timeout_sec: float = 3.0) -> None:
        with self._lock:
            self._shutting_down = True
            active_tasks = [t for t in self._tasks.values() if t.state in ACTIVE_STATES]
            for t in active_tasks:
                t.cancel_event.set()
                if t.future is not None:
                    t.future.cancel()
            procs = list(self._subprocesses.values())
            self._subprocesses.clear()

        for proc in procs:
            self._terminate_proc(proc, wait_sec=min(1.0, timeout_sec / max(1, len(procs))))

        try:
            self._io_pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._io_pool.shutdown(wait=False)
        except Exception:
            pass

        try:
            self._ai_pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._ai_pool.shutdown(wait=False)
        except Exception:
            pass
