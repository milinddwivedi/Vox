"""
VOX Isolated Local LLM Server Manager (llama-server.exe via llama.cpp).

Manages the single local Meeting Intelligence LLM (`Qwen3.5-4B-Q4_K_M.gguf`)
via an isolated loopback HTTP server (`127.0.0.1:<ephemeral_port>`):
- Vulkan GPU acceleration by default when >= 2.6 GB VRAM is available
- Explicit, non-silent CPU fallback when Vulkan cannot start or VRAM is constrained
- Idle auto-unload timer to release VRAM & RAM cleanly
- Guaranteed process cleanup on shutdown and exit
"""

from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from voxlocal.core.hardware import compute_safe_bg_threads
from voxlocal.core.logger import get_app_root, get_logger, get_user_data_root, get_user_models_root
from voxlocal.engine.meeting_model_manager import resolve_llama_server_bin

logger = get_logger("engine.llama_server")


def _strip_think_blocks(text: str) -> str:
    s = re.sub(r"<think>.*?</think>", "", str(text or ""), flags=re.DOTALL)
    return s.strip()


class LlamaServerManager:
    """Lifecycle and OpenAI-compatible HTTP client for local `llama-server.exe`."""

    DEFAULT_MODEL_NAME = "Qwen3.5-4B-Q4_K_M.gguf"

    def __init__(
        self,
        model_path: Optional[str | Path] = None,
        device: str = "auto",
        ctx_size: int = 24576,
        idle_timeout_sec: int = 300,
    ) -> None:
        if model_path is None:
            self.model_path = (get_user_models_root() / "meeting_llm" / self.DEFAULT_MODEL_NAME).resolve()
        else:
            p = Path(model_path).resolve()
            if p.is_dir():
                p = p / self.DEFAULT_MODEL_NAME
            self.model_path = p

        self.requested_device = str(device or "auto").strip().lower()
        self.ctx_size = int(ctx_size)
        self.idle_timeout_sec = max(30, int(idle_timeout_sec))

        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen] = None
        self._port: Optional[int] = None
        self._active_device: str = "unloaded"
        self._active_ctx_size: int = int(ctx_size)
        self._active_ngl: int = 0
        self._pending_ngl: int = 99
        self._pending_ctx: Optional[int] = None
        self._fallback_reason: str = ""
        self._last_error: str = ""
        self._last_used_mono: float = 0.0
        self._idle_timer: Optional[threading.Timer] = None
        self._log_file_handle: Optional[Any] = None
        self._log_start_offset: int = 0
        self._last_usage: Dict[str, Any] = {}
        self._shutting_down: bool = False
        self._active_requests: int = 0
        self._active_stream_resp: Optional[Any] = None

        atexit.register(self.stop)

    def is_available(self) -> bool:
        if not self.model_path.is_file() or self.model_path.stat().st_size < 100_000_000:
            return False
        srv_gpu, _ = resolve_llama_server_bin(prefer_gpu=True)
        srv_cpu, _ = resolve_llama_server_bin(prefer_gpu=False)
        return (srv_gpu is not None) or (srv_cpu is not None)

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def active_device(self) -> str:
        with self._lock:
            return self._active_device if self.is_running else "unloaded"

    @property
    def active_ctx_size(self) -> int:
        with self._lock:
            return self._active_ctx_size

    @property
    def fallback_reason(self) -> str:
        return self._fallback_reason

    @staticmethod
    def _get_memory_mb() -> Tuple[Optional[int], Optional[int]]:
        """Return (total_ram_mb, available_ram_mb) on Windows via GlobalMemoryStatusEx."""
        if os.name != "nt":
            return None, None
        try:
            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            st = _MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(st)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                mb = 1024 * 1024
                return int(st.ullTotalPhys // mb), int(st.ullAvailPhys // mb)
        except Exception:
            pass
        return None, None

    @staticmethod
    def _get_free_vram_mb() -> Optional[int]:
        try:
            kw: Dict[str, Any] = dict(capture_output=True, text=True, timeout=1.5)
            if os.name == "nt":
                kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,nounits,noheader"],
                **kw,
            )
            if res.returncode == 0 and res.stdout.strip():
                vals = [int(ln.strip()) for ln in res.stdout.strip().splitlines() if ln.strip().isdigit()]
                return max(vals) if vals else None
        except Exception:
            pass
        return None

    @staticmethod
    def _pick_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    def get_info(self) -> Dict[str, Any]:
        srv_gpu, _ = resolve_llama_server_bin(prefer_gpu=True)
        srv_cpu, _ = resolve_llama_server_bin(prefer_gpu=False)
        return {
            "name": self.model_path.name,
            "path": str(self.model_path),
            "available": self.is_available(),
            "loaded": self.is_running,
            "active_device": self.active_device,
            "requested_device": self.requested_device,
            "fallback_reason": self._fallback_reason,
            "vulkan_bin": str(srv_gpu) if srv_gpu else None,
            "cpu_bin": str(srv_cpu) if srv_cpu else None,
            "port": self._port if self.is_running else None,
            "error": self._last_error or None,
        }

    def _schedule_idle_unload(self) -> None:
        if self._idle_timer is not None:
            try:
                self._idle_timer.cancel()
            except Exception:
                pass
            self._idle_timer = None
        if self._shutting_down:
            return
        t = threading.Timer(float(self.idle_timeout_sec), self._on_idle_timeout)
        t.daemon = True
        t.start()
        self._idle_timer = t

    def _on_idle_timeout(self) -> None:
        with self._lock:
            if not self.is_running:
                return
            if self._active_requests > 0:
                self._last_used_mono = time.monotonic()
                self._schedule_idle_unload()
                return
            idle_for = time.monotonic() - self._last_used_mono
            if idle_for >= (self.idle_timeout_sec - 1.0):
                logger.info("Unloading idle llama-server.exe after %.0fs inactivity to free VRAM/RAM.", idle_for)
                self.stop()
            else:
                self._schedule_idle_unload()

    def _launch_variant(self, bin_path: Path, variant: str) -> Tuple[bool, str]:
        port = self._pick_free_port()
        log_dir = get_user_data_root() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "llama_server.log"

        cmd: List[str] = [
            str(bin_path),
            "-m", str(self.model_path),
            "--host", "127.0.0.1",
            "--port", str(port),
            "-np", "1",
            "-lv", "4",
            "-rea", "off",
            "--reasoning-budget", "0",
            "--jinja",
        ]
        threads = compute_safe_bg_threads()
        if variant == "vulkan":
            ngl = int(getattr(self, "_pending_ngl", 99))
            launch_ctx = int(getattr(self, "_pending_ctx", None) or self.ctx_size)
            cmd.extend(["-ngl", str(ngl), "-t", str(threads), "-c", str(launch_ctx), "-ctk", "q8_0", "-ctv", "q8_0"])
        else:
            ngl = 0
            total_mb, avail_mb = self._get_memory_mb()
            low_mem = (avail_mb is not None and avail_mb < 5500) or (total_mb is not None and total_mb < 14000)
            launch_ctx = min(8192, self.ctx_size) if low_mem else min(16384, self.ctx_size)
            cmd.extend(["-ngl", "0", "-t", str(threads), "-c", str(launch_ctx), "-ctk", "q8_0", "-ctv", "q8_0"])

        try:
            self._log_start_offset = log_path.stat().st_size if log_path.exists() else 0
            self._log_file_handle = open(log_path, "ab")
            env = dict(os.environ)
            env["PATH"] = str(bin_path.parent) + os.pathsep + env.get("PATH", "")
            creationflags = (
                (getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000))
                if os.name == "nt"
                else 0
            )
            proc = subprocess.Popen(
                cmd,
                cwd=str(bin_path.parent),
                stdout=self._log_file_handle,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=creationflags,
            )
        except Exception as exc:
            if self._log_file_handle is not None:
                try:
                    self._log_file_handle.close()
                except Exception:
                    pass
                self._log_file_handle = None
            return False, f"Failed spawning {bin_path.name} ({variant}): {exc}"

        deadline = time.monotonic() + 45.0
        health_url = f"http://127.0.0.1:{port}/health"
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return False, f"llama-server ({variant}) exited immediately with code {proc.returncode}"
            try:
                with urllib.request.urlopen(health_url, timeout=1.5) as resp:
                    if resp.status == 200:
                        self._proc = proc
                        self._port = port
                        self._active_device = f"vulkan_gpu" if variant == "vulkan" else "cpu"
                        self._active_ctx_size = launch_ctx
                        self._active_ngl = ngl
                        self._last_used_mono = time.monotonic()
                        self._schedule_idle_unload()
                        return True, f"Started {variant} on 127.0.0.1:{port}"
            except Exception:
                time.sleep(0.35)

        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except Exception:
            proc.kill()
        return False, f"llama-server ({variant}) health-check timed out after 45s"

    def ensure_started(self) -> bool:
        with self._lock:
            if self._shutting_down:
                return False
            if self.is_running:
                self._last_used_mono = time.monotonic()
                self._schedule_idle_unload()
                return True

            if not self.model_path.is_file():
                self._last_error = f"Model missing at {self.model_path}"
                return False

            self._fallback_reason = ""
            self._last_error = ""

            self._pending_ngl = 99
            self._pending_ctx = None
            want_gpu = self.requested_device in ("auto", "gpu", "vulkan", "cuda")
            if want_gpu and self.requested_device == "auto":
                free_mb = self._get_free_vram_mb()
                if free_mb is None:
                    want_gpu = False
                    self._fallback_reason = (
                        "No discrete GPU with >= 1600 MB free VRAM detected "
                        "(integrated graphics safe mode); running on CPU."
                    )
                elif free_mb >= 3800:
                    self._pending_ngl = 99
                    self._pending_ctx = None
                elif free_mb >= 1600:
                    # Adaptive partial Vulkan GPU offload (e.g. 4 GB GTX 1650 sharing desktop VRAM)
                    self._pending_ngl = max(12, min(28, int((free_mb - 650) // 75)))
                    self._pending_ctx = min(16384, self.ctx_size)
                else:
                    want_gpu = False
                    self._fallback_reason = f"Free GPU VRAM ({free_mb} MB) < 1600 MB required for Vulkan GPU offload; falling back to CPU."

            if want_gpu:
                srv_gpu, var_gpu = resolve_llama_server_bin(prefer_gpu=True)
                if srv_gpu is not None and var_gpu == "vulkan":
                    ok, msg = self._launch_variant(srv_gpu, "vulkan")
                    if ok:
                        logger.info("llama-server started: %s (%s)", self.model_path.name, msg)
                        return True
                    self._fallback_reason = f"Vulkan GPU failed ({msg}); fell back to CPU."
                    logger.warning("%s", self._fallback_reason)
                elif not self._fallback_reason:
                    self._fallback_reason = "Vulkan llama-server binary missing; fell back to CPU."

            total_mb, avail_mb = self._get_memory_mb()
            if avail_mb is not None and avail_mb < 2600:
                self._fallback_reason = (
                    f"Available RAM ({avail_mb} MB) < 2600 MB safe floor; skipping local LLM to keep system responsive."
                )
                self._last_error = self._fallback_reason
                logger.warning("%s", self._fallback_reason)
                return False
            if total_mb is not None and total_mb < 14000:
                self.idle_timeout_sec = min(self.idle_timeout_sec, 90)

            srv_cpu, _ = resolve_llama_server_bin(prefer_gpu=False)
            if srv_cpu is None:
                self._last_error = "No llama-server.exe binary found under runtime/llama_cpp/"
                return False

            ok_cpu, msg_cpu = self._launch_variant(srv_cpu, "cpu")
            if ok_cpu:
                if not self._fallback_reason and self.requested_device == "cpu":
                    self._fallback_reason = "Configured for CPU execution."
                logger.info("llama-server started: %s (%s)", self.model_path.name, msg_cpu)
                return True

            self._last_error = msg_cpu
            logger.error("llama-server failed to start: %s", msg_cpu)
            return False

    def stop(self) -> None:
        with self._lock:
            if self._idle_timer is not None:
                try:
                    self._idle_timer.cancel()
                except Exception:
                    pass
                self._idle_timer = None
            proc = self._proc
            self._proc = None
            self._port = None
            self._active_device = "unloaded"
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3.0)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=2.0)
                    except Exception:
                        pass
            if self._log_file_handle is not None:
                try:
                    self._log_file_handle.close()
                except Exception:
                    pass
                self._log_file_handle = None

    def cancel_active_stream(self) -> None:
        """Close any active streaming HTTP response so llama-server aborts immediately."""
        with self._lock:
            resp = self._active_stream_resp
            self._active_stream_resp = None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    def shutdown(self) -> None:
        self._shutting_down = True
        self.cancel_active_stream()
        self.stop()

    def get_last_usage(self) -> Dict[str, Any]:
        return dict(self._last_usage)

    def get_gpu_offload_summary(self) -> Dict[str, Any]:
        """Parse llama_server.log for the active launch to verify real Vulkan layer/KV offload."""
        log_path = get_user_data_root() / "logs" / "llama_server.log"
        summary: Dict[str, Any] = {
            "active_device": self.active_device,
            "active_ctx_size": self.active_ctx_size,
            "active_ngl": int(getattr(self, "_active_ngl", 0)),
            "vulkan_device_line": "",
            "offloaded_layers_line": "",
            "kv_buffer_lines": [],
            "model_buffer_line": "",
            "raw_excerpt": [],
        }
        if not log_path.is_file():
            return summary
        try:
            with open(log_path, "rb") as f:
                f.seek(max(0, int(self._log_start_offset or 0)))
                tail_bytes = f.read(256_000)
            text = tail_bytes.decode("utf-8", errors="ignore")
            for ln in text.splitlines():
                s = ln.strip()
                if not s:
                    continue
                low = s.lower()
                if "ggml_vulkan:" in low and ("found" in low or "nvidia" in low or "amd" in low or "intel" in low or "| 0 " in low):
                    if not summary["vulkan_device_line"]:
                        summary["vulkan_device_line"] = s
                    summary["raw_excerpt"].append(s)
                elif "offloaded" in low and "layers to gpu" in low:
                    summary["offloaded_layers_line"] = s
                    summary["raw_excerpt"].append(s)
                elif ("vulkan0" in low or "cpu_mapped" in low or "cpu" in low) and "buffer size" in low:
                    if "kv" in low or "kv_self" in low:
                        summary["kv_buffer_lines"].append(s)
                    elif not summary["model_buffer_line"] and "model buffer" in low:
                        summary["model_buffer_line"] = s
                    summary["raw_excerpt"].append(s)
            summary["raw_excerpt"] = summary["raw_excerpt"][:20]
        except Exception as exc:
            summary["error"] = str(exc)
        return summary

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        max_tokens: int = 1200,
        temperature: float = 0.1,
        json_mode: bool = False,
        json_schema: Optional[Dict[str, Any]] = None,
        schema_name: str = "structured_output",
        timeout_sec: int = 240,
    ) -> Optional[str]:
        if not self.ensure_started():
            return None

        payload: Dict[str, Any] = {
            "model": self.model_path.name,
            "messages": messages,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        schema_fallback_reason = ""
        used_constrained_schema = bool(json_schema)
        if json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": str(schema_name or "structured_output"),
                    "strict": True,
                    "schema": json_schema,
                },
            }
        elif json_mode:
            payload["response_format"] = {"type": "json_object"}

        for attempt in (1, 2):
            with self._lock:
                port = self._port
                self._last_used_mono = time.monotonic()
            if not port:
                if not self.ensure_started():
                    return None
                port = self._port

            url = f"http://127.0.0.1:{port}/v1/chat/completions"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            eff_timeout = max(int(timeout_sec), 900) if self._active_device == "cpu" else max(int(timeout_sec), 600)
            with self._lock:
                self._active_requests += 1
            try:
                with urllib.request.urlopen(req, timeout=eff_timeout) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                self._last_used_mono = time.monotonic()
                usage = dict(raw.get("usage") or {})
                choices = raw.get("choices") or []
                if choices:
                    msg_obj = choices[0].get("message") or {}
                    content = msg_obj.get("content") or ""
                    reasoning_content = msg_obj.get("reasoning_content") or ""
                    has_think_tags = "<think>" in str(content)
                    self._last_usage = {
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "reasoning_content_len": len(str(reasoning_content).strip()),
                        "has_think_tags": bool(has_think_tags),
                        "thinking_disabled_verified": (not has_think_tags) and len(str(reasoning_content).strip()) == 0,
                        "finish_reason": choices[0].get("finish_reason"),
                        "constrained_decoding_used": used_constrained_schema,
                        "schema_fallback_reason": schema_fallback_reason,
                    }
                    return _strip_think_blocks(content)
                return None
            except urllib.error.HTTPError as exc:
                err_body = ""
                try:
                    err_body = exc.read().decode("utf-8", errors="ignore")[:400]
                except Exception:
                    pass
                self._last_error = f"{exc}: {err_body}".strip()
                logger.warning("llama-server chat HTTP error (attempt %d): %s | %s", attempt, exc, err_body)
                if attempt == 1:
                    low_err = err_body.lower()
                    if "chat_template_kwargs" in err_body and "chat_template_kwargs" in payload:
                        payload.pop("chat_template_kwargs", None)
                        continue
                    if used_constrained_schema and any(k in low_err for k in ("schema", "grammar", "response_format")):
                        used_constrained_schema = False
                        schema_fallback_reason = f"Constrained json_schema fallback to json_object: {err_body[:160]}"
                        payload["response_format"] = {"type": "json_object"}
                        logger.warning("%s", schema_fallback_reason)
                        continue
                    self.stop()
                    if not self.ensure_started():
                        return None
                    continue
                return None
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning("llama-server chat error (attempt %d): %s", attempt, exc)
                if attempt == 1:
                    self.stop()
                    if not self.ensure_started():
                        return None
                    continue
                return None
            finally:
                with self._lock:
                    if self._active_requests > 0:
                        self._active_requests -= 1
                    self._last_used_mono = time.monotonic()
        return None

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        *,
        max_tokens: int = 700,
        temperature: float = 0.15,
        timeout_sec: int = 180,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Iterator[str]:
        if not self.ensure_started():
            return
        with self._lock:
            port = self._port
            self._last_used_mono = time.monotonic()
        if not port:
            return

        payload = {
            "model": self.model_path.name,
            "messages": messages,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "stream": True,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        url = f"http://127.0.0.1:{port}/v1/chat/completions"
        in_think = False
        eff_timeout = max(int(timeout_sec), 900) if self._active_device == "cpu" else int(timeout_sec)
        with self._lock:
            self._active_requests += 1
        try:
            resp = None
            for attempt in (1, 2):
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    resp = urllib.request.urlopen(req, timeout=eff_timeout)
                    break
                except urllib.error.HTTPError as exc:
                    err_body = ""
                    try:
                        err_body = exc.read().decode("utf-8", errors="ignore")[:300]
                    except Exception:
                        pass
                    if attempt == 1 and "chat_template_kwargs" in err_body and "chat_template_kwargs" in payload:
                        payload.pop("chat_template_kwargs", None)
                        continue
                    raise
            if resp is None:
                return
            with self._lock:
                self._active_stream_resp = resp
            with resp:
                for raw_line in resp:
                    if cancel_check and cancel_check():
                        break
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        obj = json.loads(data_str)
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        delta = ((choices[0].get("delta") or {}).get("content")) or ""
                        if not delta:
                            continue
                        if "<think>" in delta:
                            in_think = True
                        if in_think:
                            if "</think>" in delta:
                                delta = delta.split("</think>", 1)[1]
                                in_think = False
                            else:
                                continue
                        if delta:
                            self._last_used_mono = time.monotonic()
                            yield delta
                    except Exception:
                        continue
        except Exception as exc:
            was_cancelled = self._shutting_down or bool(cancel_check and cancel_check())
            if not was_cancelled:
                self._last_error = str(exc)
                logger.warning("llama-server streaming error: %s", exc)
        finally:
            with self._lock:
                if self._active_stream_resp is resp:
                    self._active_stream_resp = None
                if self._active_requests > 0:
                    self._active_requests -= 1
                self._last_used_mono = time.monotonic()
