"""
VOX Local LLM Engine — 100% Offline Cognitive Synthesis & Meeting Intelligence.

Powered by CTranslate2 and Tokenizers for ultra-fast, local INT8 inference
on NVIDIA GPUs (CUDA) with graceful, automatic multi-threaded CPU fallback.
Never connects to external clouds or third-party APIs.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from voxlocal.core.logger import get_app_root, get_logger

logger = get_logger("llm_engine")


def _setup_cuda_dll_directories() -> None:
    """Ensure NVIDIA CUDA runtime DLLs (cuBLAS, cuDNN, etc.) are discoverable by Windows."""
    if sys.platform != "win32":
        return

    # Check PyInstaller bundle
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass)
        for cand in [p, p / "nvidia" / "cublas" / "bin", p / "nvidia" / "cudnn" / "bin"]:
            if cand.is_dir():
                try:
                    os.add_dll_directory(str(cand))
                    os.environ["PATH"] = str(cand) + os.pathsep + os.environ.get("PATH", "")
                except Exception:
                    pass
        return

    # Check local virtual environment site-packages
    root = get_app_root()
    venv_nvidia = root / ".venv" / "Lib" / "site-packages" / "nvidia"
    if venv_nvidia.is_dir():
        for sub in venv_nvidia.glob("*/bin"):
            if sub.is_dir():
                try:
                    os.add_dll_directory(str(sub))
                    os.environ["PATH"] = str(sub) + os.pathsep + os.environ.get("PATH", "")
                except Exception:
                    pass


# Initialize DLL paths immediately
_setup_cuda_dll_directories()


class LocalLLMEngine:
    """
    Production-grade local LLM engine for VOX.
    Wraps CTranslate2 Generator and HuggingFace Tokenizers with INT8 quantization.
    Features:
    - Lazy loading: zero impact on cold startup time.
    - Automatic device detection: CUDA acceleration with automatic CPU fallback.
    - Structured MoM generation: extracts genuine decisions, actions, and summaries.
    - Grounded Q&A for Ask Vox: answers questions citing verified timestamps and speakers.
    """

    def __init__(
        self,
        model_dir: Optional[str | Path] = None,
        device: str = "auto",
        compute_type: str = "int8",
        intra_threads: int = 4,
    ) -> None:
        if model_dir is None:
            root = get_app_root()
            self.model_dir = root / "data" / "models" / "llm" / "Llama-3.2-1B-Instruct-ct2-int8"
        else:
            self.model_dir = Path(model_dir)

        self.requested_device = device
        self.compute_type = compute_type
        self.intra_threads = max(1, intra_threads)

        self._generator: Optional[Any] = None
        self._tokenizer: Optional[Any] = None
        self._active_device: Optional[str] = None
        self._load_failed: bool = False
        self._load_error_msg: str = ""
        self._is_loading: bool = False

    @staticmethod
    def _get_free_vram_mb() -> Optional[int]:
        """Query available GPU VRAM in megabytes using nvidia-smi with a strict timeout."""
        try:
            import subprocess
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,nounits,noheader"],
                capture_output=True,
                text=True,
                timeout=1.2,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if res.returncode == 0 and res.stdout.strip():
                vals = [int(line.strip()) for line in res.stdout.strip().splitlines() if line.strip().isdigit()]
                return max(vals) if vals else None
        except Exception:
            pass
        return None

    def _fallback_to_cpu(self) -> bool:
        """Dynamically unload CUDA generator and reload on multi-threaded CPU."""
        try:
            logger.warning("Unloading CUDA Local LLM Generator and falling back to multi-threaded CPU...")
            self._generator = None
            self._active_device = None
            import ctranslate2
            cpu_threads = min(8, max(2, os.cpu_count() or 4))
            self._generator = ctranslate2.Generator(
                str(self.model_dir),
                device="cpu",
                compute_type="int8",
                intra_threads=cpu_threads,
            )
            self._active_device = "cpu"
            self._load_failed = False
            logger.info("Local LLM successfully recovered on CPU (threads=%d)", cpu_threads)
            return True
        except Exception as exc:
            self._load_failed = True
            self._load_error_msg = str(exc)
            logger.error("Failed fallback to CPU for Local LLM: %s", exc, exc_info=True)
            return False

    def is_available(self) -> bool:
        """Check if model weights and tokenizer exist on disk and model didn't fatally fail to load."""
        if self._load_failed:
            return False
        if not self.model_dir.is_dir():
            return False
        model_bin = self.model_dir / "model.bin"
        tok_file = self.model_dir / "tokenizer.json"
        return model_bin.is_file() and tok_file.is_file()

    def get_model_info(self) -> Dict[str, Any]:
        """Return diagnostic metadata about the active LLM engine."""
        return {
            "name": self.model_dir.name,
            "path": str(self.model_dir),
            "available": self.is_available(),
            "loaded": self._generator is not None,
            "active_device": self._active_device or "unloaded",
            "requested_device": self.requested_device,
            "compute_type": self.compute_type,
            "threads": self.intra_threads,
            "error": self._load_error_msg if self._load_failed else None,
        }

    def _ensure_loaded(self) -> bool:
        """Lazily load generator and tokenizer into RAM/VRAM on first request."""
        if self._generator is not None and self._tokenizer is not None:
            return True

        if not self.is_available():
            logger.warning("Local LLM model files not found at %s", self.model_dir)
            return False

        if self._is_loading:
            return False

        self._is_loading = True
        t0 = time.perf_counter()

        try:
            import ctranslate2
            from tokenizers import Tokenizer

            # 1. Load Tokenizer
            tok_path = self.model_dir / "tokenizer.json"
            self._tokenizer = Tokenizer.from_file(str(tok_path))

            # 2. Determine target device
            use_cuda = False
            if self.requested_device.lower() in ("auto", "cuda"):
                try:
                    cuda_count = ctranslate2.get_cuda_device_count()
                    if cuda_count > 0:
                        if self.requested_device.lower() == "auto":
                            free_mb = self._get_free_vram_mb()
                            if free_mb is not None and free_mb < 1400:
                                logger.info(
                                    "Low available GPU VRAM (%d MB free < 1400 MB). Selecting multi-threaded CPU for LLM to avoid CUDA out-of-memory.",
                                    free_mb,
                                )
                                use_cuda = False
                            else:
                                use_cuda = True
                        else:
                            use_cuda = True
                except Exception as exc:
                    logger.debug("CUDA check returned: %s", exc)

            # 3. Load Generator with CUDA or CPU fallback
            if use_cuda:
                try:
                    logger.info("Loading Local LLM on CUDA (compute_type=%s)...", self.compute_type)
                    self._generator = ctranslate2.Generator(
                        str(self.model_dir),
                        device="cuda",
                        compute_type=self.compute_type,
                        inter_threads=1,
                        intra_threads=self.intra_threads,
                    )
                    self._active_device = "cuda"
                    logger.info("Local LLM successfully initialized on CUDA in %.2fs", time.perf_counter() - t0)
                    self._load_failed = False
                    return True
                except Exception as cuda_exc:
                    logger.warning("CUDA initialization failed (%s). Falling back to multi-threaded CPU...", cuda_exc)

            # Fallback to CPU
            cpu_threads = min(8, max(2, os.cpu_count() or 4))
            logger.info("Loading Local LLM on CPU (compute_type=int8, threads=%d)...", cpu_threads)
            self._generator = ctranslate2.Generator(
                str(self.model_dir),
                device="cpu",
                compute_type="int8",
                intra_threads=cpu_threads,
            )
            self._active_device = "cpu"
            logger.info("Local LLM successfully initialized on CPU in %.2fs", time.perf_counter() - t0)
            self._load_failed = False
            return True

        except Exception as exc:
            self._load_failed = True
            self._load_error_msg = str(exc)
            logger.error("Failed to initialize Local LLM Generator: %s", exc, exc_info=True)
            return False
        finally:
            self._is_loading = False

    def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.2,
        top_p: float = 0.9,
        stop_tokens: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Low-level text generation from a formatted prompt string."""
        if not self._ensure_loaded():
            return None

        if stop_tokens is None:
            stop_tokens = ["<|eot_id|>", "<|end_of_text|>"]

        try:
            tokens = self._tokenizer.encode(prompt).tokens
            results = self._generator.generate_batch(
                [tokens],
                max_length=max_tokens,
                sampling_topp=top_p,
                sampling_temperature=temperature,
                include_prompt_in_result=False,
                end_token=stop_tokens,
            )
            out_tokens = results[0].sequences[0]
            out_ids = [self._tokenizer.token_to_id(t) for t in out_tokens if self._tokenizer.token_to_id(t) is not None]
            generated_text = self._tokenizer.decode(out_ids)
            return generated_text.strip()
        except Exception as exc:
            err_str = str(exc).lower()
            if self._active_device == "cuda" and ("out of memory" in err_str or "cuda" in err_str):
                logger.warning("CUDA runtime failure during LLM generation (%s). Triggering automatic CPU recovery...", exc)
                if self._fallback_to_cpu():
                    try:
                        results = self._generator.generate_batch(
                            [tokens],
                            max_length=max_tokens,
                            sampling_topp=top_p,
                            sampling_temperature=temperature,
                            include_prompt_in_result=False,
                            end_token=stop_tokens,
                        )
                        out_tokens = results[0].sequences[0]
                        out_ids = [self._tokenizer.token_to_id(t) for t in out_tokens if self._tokenizer.token_to_id(t) is not None]
                        generated_text = self._tokenizer.decode(out_ids)
                        return generated_text.strip()
                    except Exception as cpu_exc:
                        logger.error("CPU fallback generation also failed: %s", cpu_exc, exc_info=True)
            logger.error("Generation error in LocalLLMEngine: %s", exc, exc_info=True)
            return None

    def generate_stream(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.2,
        top_p: float = 0.9,
        stop_tokens: Optional[List[str]] = None,
    ) -> Iterator[str]:
        """
        Stream generated tokens in real time from a prompt string.
        Yields incremental decoded string chunks as each token is produced.
        """
        if not self._ensure_loaded():
            return

        if stop_tokens is None:
            stop_tokens = ["<|eot_id|>", "<|end_of_text|>"]

        try:
            tokens = self._tokenizer.encode(prompt).tokens
            step_results = self._generator.generate_tokens(
                tokens,
                max_length=max_tokens,
                sampling_topp=top_p,
                sampling_temperature=temperature,
                end_token=stop_tokens,
            )
            accumulated_ids: List[int] = []
            accumulated_text = ""
            for step in step_results:
                if step.token_id is not None:
                    accumulated_ids.append(step.token_id)
                    decoded = self._tokenizer.decode(accumulated_ids)
                    if len(decoded) > len(accumulated_text):
                        chunk = decoded[len(accumulated_text):]
                        accumulated_text = decoded
                        yield chunk
        except Exception as exc:
            err_str = str(exc).lower()
            if self._active_device == "cuda" and ("out of memory" in err_str or "cuda" in err_str):
                logger.warning("CUDA runtime failure during streaming LLM generation (%s). Triggering automatic CPU recovery...", exc)
                if self._fallback_to_cpu():
                    try:
                        step_results = self._generator.generate_tokens(
                            tokens,
                            max_length=max_tokens,
                            sampling_topp=top_p,
                            sampling_temperature=temperature,
                            end_token=stop_tokens,
                        )
                        accumulated_ids = []
                        accumulated_text = ""
                        for step in step_results:
                            if step.token_id is not None:
                                accumulated_ids.append(step.token_id)
                                decoded = self._tokenizer.decode(accumulated_ids)
                                if len(decoded) > len(accumulated_text):
                                    chunk = decoded[len(accumulated_text):]
                                    accumulated_text = decoded
                                    yield chunk
                        return
                    except Exception as cpu_exc:
                        logger.error("CPU fallback streaming also failed: %s", cpu_exc, exc_info=True)
            logger.error("Streaming generation error in LocalLLMEngine: %s", exc, exc_info=True)

    def generate_meeting_analysis(
        self,
        segments: List[Dict[str, Any]],
        title: str = "Meeting Notes",
        platform: str = "Microsoft Teams",
        duration_sec: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Analyze meeting transcript evidence and extract structured intelligence:
        - Executive Summary
        - Discussion Topics
        - Decisions (with category, rationale, and quote)
        - Action Items (with owner, task, due date, and quote)
        - Open Questions
        - Risks and Blockers
        """
        if not segments:
            return None

        # Build chronological dialogue transcript
        dialogue_lines: List[str] = []
        for s in segments:
            st = float(s.get("start_time", 0.0))
            mm, ss = divmod(int(st), 60)
            spk = str(s.get("speaker", "Speaker")).strip()
            txt = str(s.get("text", "")).strip()
            if txt:
                dialogue_lines.append(f"[{mm:02d}:{ss:02d}] {spk}: {txt}")

        if not dialogue_lines:
            return None

        full_dialogue = "\n".join(dialogue_lines)

        # For long transcripts (> 6,000 words), use stratified chronological sampling with cue preservation
        # so middle segments of long meetings are never omitted
        words = full_dialogue.split()
        if len(words) > 6000:
            head_lines = []
            tail_lines = []
            mid_lines = []
            n_lines = len(dialogue_lines)
            idx_head_end = max(1, int(n_lines * 0.25))
            idx_tail_start = min(n_lines - 1, int(n_lines * 0.75))

            cue_pattern = re.compile(
                r"\b(decided|agreed|approved|confirmed|concluded|settled|will|action|task|deadline|owner|risk|blocker|\?)\b",
                re.IGNORECASE,
            )

            for i, line in enumerate(dialogue_lines):
                if i < idx_head_end:
                    head_lines.append(line)
                elif i >= idx_tail_start:
                    tail_lines.append(line)
                else:
                    if cue_pattern.search(line) or (i % 2 == 0):
                        mid_lines.append(line)

            analysis_transcript = (
                "\n".join(head_lines)
                + "\n\n[... middle discussion section ...]\n\n"
                + "\n".join(mid_lines)
                + "\n\n[... concluding section ...]\n\n"
                + "\n".join(tail_lines)
            )
            w_split = analysis_transcript.split()
            if len(w_split) > 7500:
                analysis_transcript = " ".join(w_split[:7500])
        else:
            analysis_transcript = full_dialogue

        dur_min, dur_sec = divmod(int(duration_sec), 60)
        dur_str = f"{dur_min}m {dur_sec}s" if dur_min > 0 else f"{dur_sec}s"

        prompt = (
            "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n"
            "You are an expert executive meeting intelligence analyst for VOX.\n"
            "Analyze the meeting transcript below and extract structured, verified meeting intelligence.\n"
            "Strictly adhere to the facts stated in the transcript. Do NOT hallucinate or extrapolate.\n"
            "Respond ONLY with a valid JSON object matching this schema:\n"
            "{\n"
            '  "executive_summary": "2 to 3 concise paragraphs summarizing the primary purpose, discussions, and outcomes.",\n'
            '  "topics": [\n'
            '    {"title": "Topic title", "summary": "Key discussion summary"}\n'
            "  ],\n"
            '  "decisions": [\n'
            '    {"decision": "Clear statement of decision", "category": "Strategic" | "Technical" | "Product" | "Budget" | "Process" | "General", "status": "confirmed" | "proposed" | "rejected" | "unresolved" | "unclassified", "evolution_relationship": "new" | "repeated" | "superseded" | "reversed", "rationale": "Why it was decided", "evidence_quote": "Verbatim quote or close excerpt", "timestamp_str": "MM:SS"}\n'
            "  ],\n"
            '  "action_items": [\n'
            '    {"description": "Clear action verb initiated task", "owner": "Person name or Role", "due_date": "Exact date, spoken relative phrase e.g. \'by Friday\' or \'in 2 weeks\', or \'Not specified\'", "priority": "High" | "Medium" | "Low", "evidence_quote": "Excerpt", "timestamp_str": "MM:SS"}\n'
            "  ],\n"
            '  "open_questions": [\n'
            '    {"question": "Unresolved question", "asked_by": "Speaker name", "timestamp_str": "MM:SS"}\n'
            "  ],\n"
            '  "risks": [\n'
            '    {"risk": "Identified risk or obstacle", "impact": "Potential consequence", "mitigation": "Proposed solution or None", "timestamp_str": "MM:SS"}\n'
            "  ]\n"
            "}<|eot_id|>"
            "<|start_header_id|>user<|end_header_id|>\n\n"
            f"Meeting: {title}\n"
            f"Platform: {platform}\n"
            f"Duration: {dur_str}\n\n"
            f"TRANSCRIPT:\n{analysis_transcript}\n<|eot_id|>"
            "<|start_header_id|>assistant<|end_header_id|>\n\n"
        )

        raw_output = self.generate(prompt, max_tokens=2048, temperature=0.2, top_p=0.9)
        if not raw_output:
            return None

        parsed_json = self._parse_json_safely(raw_output)
        return parsed_json

    def answer_query(
        self,
        question: str,
        citations: List[Dict[str, Any]],
        context_meeting_title: Optional[str] = None,
    ) -> Optional[str]:
        """
        Synthesize a fluent, grounded natural language answer to an Ask Vox query
        using retrieved citations and verbatim meeting evidence.
        """
        if not citations:
            return None

        # Format citations
        formatted_excerpts: List[str] = []
        for i, c in enumerate(citations[:8], 1):
            m_title = c.get("meeting_title", "Meeting")
            st = c.get("timestamp_sec", 0.0)
            mm, ss = divmod(int(st), 60)
            spk = c.get("speaker", "Speaker")
            txt = c.get("quote") or c.get("text", "")
            formatted_excerpts.append(f"[{i}] ({m_title} at {mm:02d}:{ss:02d}) {spk}: \"{txt}\"")

        excerpts_text = "\n\n".join(formatted_excerpts)
        scope_info = f" in meeting '{context_meeting_title}'" if context_meeting_title else ""

        prompt = (
            "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n"
            "You are Vox, a professional and completely local AI voice and meeting assistant.\n"
            "Answer the user's question directly, clearly, and concisely based strictly on the provided meeting excerpts.\n"
            "Rules:\n"
            "1. Be helpful, precise, and professional.\n"
            "2. Cite the relevant speaker and timestamp [MM:SS] when stating facts.\n"
            "3. If the excerpts do not contain enough information to answer, state that honestly without making things up.\n"
            "4. Format your answer with clean Markdown (bullet points, bold highlights).<|eot_id|>"
            "<|start_header_id|>user<|end_header_id|>\n\n"
            f"Meeting Excerpts:\n{excerpts_text}\n\n"
            f"Question{scope_info}: {question}<|eot_id|>"
            "<|start_header_id|>assistant<|end_header_id|>\n\n"
        )

        answer = self.generate(prompt, max_tokens=400, temperature=0.3, top_p=0.9)
        return answer

    def answer_query_stream(
        self,
        question: str,
        citations: List[Dict[str, Any]],
        context_meeting_title: Optional[str] = None,
    ) -> Iterator[str]:
        """
        Synthesize a fluent, grounded natural language answer to an Ask Vox query
        streaming tokens incrementally in real time.
        """
        if not citations:
            return

        # Format citations
        formatted_excerpts: List[str] = []
        for i, c in enumerate(citations[:8], 1):
            m_title = c.get("meeting_title", "Meeting")
            st = c.get("timestamp_sec", 0.0)
            mm, ss = divmod(int(st), 60)
            spk = c.get("speaker", "Speaker")
            txt = c.get("quote") or c.get("text", "")
            formatted_excerpts.append(f"[{i}] ({m_title} at {mm:02d}:{ss:02d}) {spk}: \"{txt}\"")

        excerpts_text = "\n\n".join(formatted_excerpts)
        scope_info = f" in meeting '{context_meeting_title}'" if context_meeting_title else ""

        prompt = (
            "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n"
            "You are Vox, a professional and completely local AI voice and meeting assistant.\n"
            "Answer the user's question directly, clearly, and concisely based strictly on the provided meeting excerpts.\n"
            "Rules:\n"
            "1. Be helpful, precise, and professional.\n"
            "2. Cite the relevant speaker and timestamp [MM:SS] when stating facts.\n"
            "3. If the excerpts do not contain enough information to answer, state that honestly without making things up.\n"
            "4. Format your answer with clean Markdown (bullet points, bold highlights).<|eot_id|>"
            "<|start_header_id|>user<|end_header_id|>\n\n"
            f"Meeting Excerpts:\n{excerpts_text}\n\n"
            f"Question{scope_info}: {question}<|eot_id|>"
            "<|start_header_id|>assistant<|end_header_id|>\n\n"
        )

        for chunk in self.generate_stream(prompt, max_tokens=400, temperature=0.3, top_p=0.9):
            yield chunk

    @staticmethod
    def _parse_json_safely(raw_text: str) -> Optional[Dict[str, Any]]:
        """Extract and parse JSON object from model generation defensively,
        with automatic repair for truncated or trailing-comma JSON."""
        text = raw_text.strip()

        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        # Locate the opening brace
        start_idx = text.find("{")
        if start_idx == -1:
            return None

        # Build candidates: full text from first { to EOF first (to capture all fields if truncated),
        # then bounded substring from first { to last }
        candidates: List[str] = [text[start_idx:]]
        end_idx = text.rfind("}")
        if end_idx != -1 and end_idx > start_idx and (end_idx + 1) < len(text):
            candidates.append(text[start_idx : end_idx + 1])

        for cand in candidates:
            # 1. Direct parse
            try:
                data = json.loads(cand)
                if isinstance(data, dict):
                    return LocalLLMEngine._sanitize_semantic_mom(data)
            except json.JSONDecodeError:
                pass

            # 2. Trailing comma cleanup
            cleaned = re.sub(r",\s*([\]}])", r"\1", cand)
            try:
                data = json.loads(cleaned)
                if isinstance(data, dict):
                    return LocalLLMEngine._sanitize_semantic_mom(data)
            except Exception:
                pass

            # 3. Truncated recovery (closing unclosed quotes and brackets)
            repaired = LocalLLMEngine._repair_truncated_json(cleaned)
            if repaired:
                try:
                    data = json.loads(repaired)
                    if isinstance(data, dict):
                        return LocalLLMEngine._sanitize_semantic_mom(data)
                except Exception:
                    pass

        return None

    @staticmethod
    def _sanitize_semantic_mom(data: Dict[str, Any]) -> Dict[str, Any]:
        """Distinguish syntactic from semantic JSON completion by purging truncated tail fragments."""
        if not isinstance(data, dict):
            return data

        # Sanitize action items: require a non-empty description
        if "action_items" in data and isinstance(data["action_items"], list):
            cleaned_actions = []
            for a in data["action_items"]:
                if isinstance(a, dict) and str(a.get("description", "")).strip():
                    a["description"] = str(a["description"]).strip()
                    if not a.get("owner") or not str(a["owner"]).strip():
                        a["owner"] = "Unassigned — confirmation required"
                    if not a.get("due_date") or not str(a["due_date"]).strip():
                        a["due_date"] = "Not specified"
                    prio = str(a.get("priority", "Medium")).strip().capitalize()
                    a["priority"] = prio if prio in ("High", "Medium", "Low") else "Medium"
                    cleaned_actions.append(a)
            data["action_items"] = cleaned_actions

        # Sanitize decisions: require a non-empty decision, normalize decoupled fields
        if "decisions" in data and isinstance(data["decisions"], list):
            cleaned_decisions = []
            for d in data["decisions"]:
                if isinstance(d, dict) and str(d.get("decision", "")).strip():
                    d["decision"] = str(d["decision"]).strip()

                    # Normalize category: 'Strategic', 'Technical', 'Product', 'Budget', 'Process', 'General'
                    cat = str(d.get("category", "General")).strip().capitalize()
                    valid_cats = ("Strategic", "Technical", "Product", "Budget", "Process", "General")
                    if cat not in valid_cats:
                        cat = "General"
                    d["category"] = cat

                    # Normalize status: 'unclassified', 'proposed', 'confirmed', 'rejected', 'unresolved'
                    stat = str(d.get("status", "")).strip().lower()
                    if stat not in ("proposed", "confirmed", "rejected", "unresolved", "unclassified"):
                        legacy_val = str(d.get("category", "")).lower()
                        if "unresolved" in legacy_val:
                            stat = "unresolved"
                        elif "tentative" in legacy_val or "proposed" in legacy_val:
                            stat = "proposed"
                        elif "rejected" in legacy_val:
                            stat = "rejected"
                        else:
                            stat = "confirmed"
                    d["status"] = stat

                    # Normalize evolution_relationship: 'new', 'repeated', 'superseded', 'reversed'
                    evo = str(d.get("evolution_relationship", "new")).strip().lower()
                    if evo not in ("new", "repeated", "superseded", "reversed"):
                        evo = "new"
                    d["evolution_relationship"] = evo

                    cleaned_decisions.append(d)
            data["decisions"] = cleaned_decisions

        # Sanitize topics: require title or summary
        if "topics" in data and isinstance(data["topics"], list):
            data["topics"] = [
                t for t in data["topics"]
                if isinstance(t, dict) and (str(t.get("title", "")).strip() or str(t.get("summary", "")).strip())
            ]

        # Sanitize open_questions: require question
        if "open_questions" in data and isinstance(data["open_questions"], list):
            data["open_questions"] = [
                q for q in data["open_questions"]
                if isinstance(q, dict) and str(q.get("question", "")).strip()
            ]

        # Sanitize risks: require risk
        if "risks" in data and isinstance(data["risks"], list):
            data["risks"] = [
                r for r in data["risks"]
                if isinstance(r, dict) and str(r.get("risk", "")).strip()
            ]

        return data

    @staticmethod
    def _repair_truncated_json(text: str) -> Optional[str]:
        """Attempt to close unclosed strings, objects, and arrays from truncated output."""
        in_string = False
        escape = False
        stack: List[str] = []
        cleaned_chars: List[str] = []

        for ch in text:
            if escape:
                escape = False
                cleaned_chars.append(ch)
                continue
            if ch == "\\":
                escape = True
                cleaned_chars.append(ch)
                continue
            if ch == '"':
                in_string = not in_string
                cleaned_chars.append(ch)
                continue
            if not in_string:
                if ch in ("{", "["):
                    stack.append(ch)
                elif ch == "}":
                    if stack and stack[-1] == "{":
                        stack.pop()
                elif ch == "]":
                    if stack and stack[-1] == "[":
                        stack.pop()
            cleaned_chars.append(ch)

        if in_string:
            cleaned_chars.append('"')

        reconstructed = "".join(cleaned_chars).rstrip()
        # Clean up dangling colons or incomplete keys before closing
        reconstructed = re.sub(r',\s*"[^"]*"\s*:\s*$', '', reconstructed)
        reconstructed = re.sub(r'\{\s*"[^"]*"\s*:\s*$', '{', reconstructed)
        reconstructed = re.sub(r',\s*"[^"]*"\s*$', '', reconstructed)
        reconstructed = re.sub(r',\s*$', '', reconstructed)

        while stack:
            top = stack.pop()
            if top == "{":
                reconstructed += "}"
            elif top == "[":
                reconstructed += "]"

        return reconstructed
