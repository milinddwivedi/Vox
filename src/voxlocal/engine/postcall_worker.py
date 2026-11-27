"""
Isolated Subprocess Worker & Host Coordinator for Post-Call English ASR + Neural Diarization.

Root Cause Solved:
`sherpa_onnx.OfflineSpeakerDiarization.process()` holds the Python GIL for the entire
duration of C++/ONNX PyAnnote+WeSpeaker execution (up to 524s+ on 1-hour meetings).
Running it in an in-process thread freezes the PyQt6 UI event loop (`VOX (Not Responding)`).

This module executes `PostCallEnglishASRPipeline` in a separate Windows process:
- Zero GIL impact on the GUI event loop (UI remains <16ms responsive during full diarization)
- File-based stdout/stderr logging (never pipe-buffer deadlock on C++ stderr logs)
- Atomic checkpointing of decoded ASR segments before diarization begins
- Cooperative cancel + pause + hard `terminate()/kill()` support on cancellation or app exit
- Works cleanly both in Python dev mode and inside frozen PyInstaller (`VoxLocal.exe --postcall-worker`)
"""

from __future__ import annotations

import ctypes
import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from voxlocal.core.logger import get_app_root, get_logger, setup_logging

logger = get_logger("engine.postcall_worker")

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_NO_WINDOW = 0x08000000


def _atomic_write_json(target_path: Path, payload: Dict[str, Any]) -> None:
    os.makedirs(target_path.parent, exist_ok=True)
    tmp_path = target_path.with_name(f"{target_path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp_path, target_path)


def _set_below_normal_priority() -> None:
    if os.name != "nt":
        return
    try:
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass


def run_postcall_worker_cli(job_json_path: str) -> int:
    """
    Entrypoint executed inside the isolated worker subprocess via:
      `python main.py --postcall-worker <job.json>` or `VoxLocal.exe --postcall-worker <job.json>`
    """
    setup_logging()
    _set_below_normal_priority()

    job_path = Path(job_json_path)
    if not job_path.is_file():
        print(f"Post-call worker job file not found: {job_path}", file=sys.stderr)
        return 2

    try:
        spec = json.loads(job_path.read_text(encoding="utf-8"))
        progress_path = Path(spec.get("progress_path") or (job_path.parent / "progress.jsonl"))
        result_path = Path(spec.get("result_path") or (job_path.parent / "result.json"))
        checkpoint_path = Path(spec.get("checkpoint_path") or (job_path.parent / "asr_checkpoint.json"))
        cancel_flag_path = Path(spec.get("cancel_flag_path") or (job_path.parent / "cancel.flag"))
        pause_flag_path = Path(spec.get("pause_flag_path") or (job_path.parent / "pause.flag"))
    except Exception as exc:
        print(f"Invalid post-call worker job spec {job_path}: {exc}\n{traceback.format_exc()}", file=sys.stderr)
        return 2

    def _emit_progress(msg: str) -> None:
        try:
            line = json.dumps({"ts": time.time(), "message": str(msg)}, ensure_ascii=False)
            with open(progress_path, "a", encoding="utf-8") as pf:
                pf.write(line + "\n")
                pf.flush()
        except Exception:
            pass

    def _on_asr_checkpoint(raw_segments: List[Dict[str, Any]]) -> None:
        try:
            _atomic_write_json(checkpoint_path, {"segments": raw_segments, "ts": time.time()})
        except Exception:
            pass

    pipe = None
    try:
        from voxlocal.engine.meeting_asr_pipeline import PostCallEnglishASRPipeline

        pipe = PostCallEnglishASRPipeline(
            models_dir=spec.get("models_dir") or None,
            num_threads=int(spec["num_threads"]) if spec.get("num_threads") else None,
        )
        segments = pipe.transcribe_meeting_dual_track(
            mixed_wav_path=spec.get("mixed_wav_path") or "",
            mic_wav_path=spec.get("mic_wav_path") or "",
            sys_wav_path=spec.get("sys_wav_path") or "",
            vocabulary=spec.get("vocabulary") or [],
            glossary_items=spec.get("glossary_items") or [],
            speaker_aliases=spec.get("speaker_aliases") or {},
            remote_speaker_label=spec.get("remote_speaker_label") or "Speaker 1",
            mic_speaker_label=spec.get("mic_speaker_label") or "You (Mic)",
            diarization_enabled=bool(spec.get("diarization_enabled", True)),
            diarization_threshold=float(spec.get("diarization_threshold", 0.72) or 0.72),
            echo_bleed_suppression=bool(spec.get("echo_bleed_suppression", True)),
            glossary_correction=bool(spec.get("glossary_correction", True)),
            progress_callback=_emit_progress,
            cancel_check=lambda: cancel_flag_path.exists(),
            pause_check=lambda: pause_flag_path.exists(),
            asr_checkpoint_callback=_on_asr_checkpoint,
        )
        cancelled = cancel_flag_path.exists()
        _atomic_write_json(
            result_path,
            {
                "ok": not cancelled,
                "cancelled": cancelled,
                "segments": segments if not cancelled else [],
                "track_stats": dict(pipe.last_track_stats or {}),
                "diarization_stats": dict(pipe.last_diarization_stats or {}),
            },
        )
        return 0
    except Exception as exc:
        logger.error("Post-call subprocess worker failed: %s", exc, exc_info=True)
        try:
            _atomic_write_json(
                result_path,
                {
                    "ok": False,
                    "cancelled": cancel_flag_path.exists(),
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "traceback": traceback.format_exc()[-4000:],
                },
            )
        except Exception:
            pass
        return 1
    finally:
        if pipe is not None:
            try:
                pipe.unload()
            except Exception:
                pass
        gc.collect()


class PostCallSubprocessRunner:
    """
    Host-side coordinator launching & supervising the isolated post-call worker subprocess.
    Never blocks the PyQt6 UI thread and guarantees cleanup on cancel, crash, or app shutdown.
    """

    def __init__(
        self,
        *,
        models_dir: Optional[str | Path] = None,
        num_threads: Optional[int] = None,
        task_manager: Optional[Any] = None,
        task_id: str = "",
    ) -> None:
        self.models_dir = str(models_dir or "")
        self.num_threads = int(num_threads) if num_threads else None
        self.task_manager = task_manager
        self.task_id = task_id
        self.last_track_stats: Dict[str, Any] = {}
        self.last_diarization_stats: Dict[str, Any] = {}
        self.last_checkpoint_segments: List[Dict[str, Any]] = []
        self._proc: Optional[subprocess.Popen] = None
        self._cancel_flag_path: Optional[Path] = None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        if self._cancel_flag_path is not None:
            try:
                self._cancel_flag_path.write_text("cancel\n", encoding="utf-8")
            except Exception:
                pass
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
            except Exception:
                pass

    @staticmethod
    def _build_worker_cmd(job_json_path: Path) -> Tuple[List[str], Path]:
        root_dir = get_app_root()
        if getattr(sys, "frozen", False):
            exe_path = Path(sys.executable).resolve()
            return [str(exe_path), "--postcall-worker", str(job_json_path)], exe_path.parent
        main_py = root_dir / "main.py"
        if not main_py.exists():
            main_py = Path(__file__).resolve().parents[3] / "main.py"
        return [sys.executable, str(main_py), "--postcall-worker", str(job_json_path)], main_py.parent

    def run(
        self,
        *,
        mixed_wav_path: str | Path = "",
        mic_wav_path: str | Path = "",
        sys_wav_path: str | Path = "",
        vocabulary: Optional[List[Dict[str, Any]]] = None,
        glossary_items: Optional[List[Dict[str, Any]]] = None,
        speaker_aliases: Optional[Dict[str, str]] = None,
        remote_speaker_label: str = "Speaker 1",
        mic_speaker_label: str = "You (Mic)",
        diarization_enabled: bool = True,
        diarization_threshold: float = 0.72,
        echo_bleed_suppression: bool = True,
        glossary_correction: bool = True,
        progress_callback: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_check: Optional[Callable[[], bool]] = None,
        timeout_sec: float = 3600.0,
    ) -> List[Dict[str, Any]]:
        ipc_dir = Path(tempfile.mkdtemp(prefix="vox_postcall_ipc_"))
        progress_path = ipc_dir / "progress.jsonl"
        result_path = ipc_dir / "result.json"
        checkpoint_path = ipc_dir / "asr_checkpoint.json"
        cancel_flag_path = ipc_dir / "cancel.flag"
        pause_flag_path = ipc_dir / "pause.flag"
        stdio_log_path = ipc_dir / "worker_stdio.log"
        job_json_path = ipc_dir / "job.json"
        self._cancel_flag_path = cancel_flag_path

        spec = {
            "mixed_wav_path": str(mixed_wav_path or ""),
            "mic_wav_path": str(mic_wav_path or ""),
            "sys_wav_path": str(sys_wav_path or ""),
            "models_dir": self.models_dir,
            "num_threads": self.num_threads,
            "vocabulary": list(vocabulary or []),
            "glossary_items": list(glossary_items or []),
            "speaker_aliases": dict(speaker_aliases or {}),
            "remote_speaker_label": remote_speaker_label,
            "mic_speaker_label": mic_speaker_label,
            "diarization_enabled": bool(diarization_enabled),
            "diarization_threshold": float(diarization_threshold),
            "echo_bleed_suppression": bool(echo_bleed_suppression),
            "glossary_correction": bool(glossary_correction),
            "progress_path": str(progress_path),
            "result_path": str(result_path),
            "checkpoint_path": str(checkpoint_path),
            "cancel_flag_path": str(cancel_flag_path),
            "pause_flag_path": str(pause_flag_path),
        }
        job_json_path.write_text(json.dumps(spec, ensure_ascii=False, default=str), encoding="utf-8")

        cmd, cwd_dir = self._build_worker_cmd(job_json_path)
        env = os.environ.copy()
        env["VOXLOCAL_WORKER_MODE"] = "postcall"
        if not env.get("VOXLOCAL_APP_ROOT"):
            env["VOXLOCAL_APP_ROOT"] = str(get_app_root())

        creationflags = (CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS) if os.name == "nt" else 0
        progress_offset = 0
        stdio_fh = None
        t_start = time.monotonic()

        try:
            stdio_fh = open(stdio_log_path, "w", encoding="utf-8", errors="replace")
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(cwd_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdio_fh,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            if self.task_manager is not None and self.task_id:
                self.task_manager.register_subprocess(self.task_id, self._proc)

            def _drain_progress() -> None:
                nonlocal progress_offset
                if not progress_path.exists():
                    return
                try:
                    with open(progress_path, "r", encoding="utf-8", errors="replace") as pf:
                        pf.seek(progress_offset)
                        chunk = pf.read()
                        progress_offset = pf.tell()
                    if chunk and progress_callback:
                        for raw_line in chunk.splitlines():
                            raw_line = raw_line.strip()
                            if not raw_line:
                                continue
                            try:
                                obj = json.loads(raw_line)
                                msg = str(obj.get("message") or "").strip()
                                if msg:
                                    progress_callback(msg)
                            except Exception:
                                pass
                except Exception:
                    pass

            while True:
                _drain_progress()

                if self._cancelled or (cancel_check and cancel_check()):
                    self.cancel()
                    return []

                if pause_check is not None:
                    try:
                        paused = bool(pause_check())
                        if paused and not pause_flag_path.exists():
                            pause_flag_path.write_text("pause\n", encoding="utf-8")
                        elif not paused and pause_flag_path.exists():
                            pause_flag_path.unlink(missing_ok=True)
                    except Exception:
                        pass

                ret = self._proc.poll()
                if ret is not None:
                    break

                if (time.monotonic() - t_start) >= timeout_sec:
                    logger.error("Post-call worker subprocess timed out after %.1fs; terminating.", timeout_sec)
                    self.cancel()
                    raise TimeoutError(f"Post-call transcription timed out after {int(timeout_sec)}s.")

                time.sleep(0.10)

            _drain_progress()

            if checkpoint_path.exists():
                try:
                    ck = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                    self.last_checkpoint_segments = list(ck.get("segments") or [])
                except Exception:
                    self.last_checkpoint_segments = []

            if result_path.exists():
                res = json.loads(result_path.read_text(encoding="utf-8"))
                self.last_track_stats = dict(res.get("track_stats") or {})
                self.last_diarization_stats = dict(res.get("diarization_stats") or {})
                if res.get("cancelled"):
                    return []
                if res.get("ok"):
                    return list(res.get("segments") or [])
                raise RuntimeError(str(res.get("error") or "Post-call worker reported failure."))

            stdio_tail = ""
            try:
                if stdio_fh is not None:
                    stdio_fh.flush()
                if stdio_log_path.exists():
                    stdio_tail = stdio_log_path.read_text(encoding="utf-8", errors="replace")[-1200:].strip()
            except Exception:
                pass
            raise RuntimeError(
                f"Post-call worker exited unexpectedly (code={self._proc.returncode})"
                + (f": {stdio_tail}" if stdio_tail else "")
            )
        finally:
            if self.task_manager is not None and self.task_id:
                self.task_manager.unregister_subprocess(self.task_id)
            if self._proc is not None and self._proc.poll() is None:
                self.cancel()
            self._proc = None
            if stdio_fh is not None:
                try:
                    stdio_fh.close()
                except Exception:
                    pass
            shutil.rmtree(ipc_dir, ignore_errors=True)
