"""
Streaming Typer Engine for VOX.
Coordinates real-time streaming speech-to-text text insertion,
verbal self-correction backtracking, transformation mode dispatch,
and focus-invariant active cursor delivery.
"""

from __future__ import annotations

import enum
import time
from typing import Any, Callable, Dict, List, Optional

from voxlocal.core.logger import get_logger
from voxlocal.core.vocabulary_manager import VocabularyManager
from voxlocal.engine.dictation_processor import DictationProcessor, TransformationMode
from voxlocal.win32.text_inserter import TextInserter

logger = get_logger("engine.streaming_typer")


class TyperState(str, enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    COMMITTED = "committed"
    ERROR = "error"


class StreamingTyper:
    """
    Orchestrates real-time speech token streaming, self-correction rewriting,
    mode-based post-processing, and caret injection.
    """

    def __init__(
        self,
        text_inserter: Optional[TextInserter] = None,
        vocabulary_manager: Optional[VocabularyManager] = None,
        mode: TransformationMode = TransformationMode.MAGIC,
    ) -> None:
        if text_inserter is None:
            try:
                from voxlocal.win32.window_focus import WindowFocusTracker
                self.text_inserter = TextInserter(WindowFocusTracker())
            except Exception:
                self.text_inserter = None
        else:
            self.text_inserter = text_inserter

        self.vocabulary_manager = vocabulary_manager or VocabularyManager()
        self._mode = mode
        self._state = TyperState.IDLE

        # Buffers
        self._provisional_buffer: str = ""
        self._committed_text: str = ""
        self._retracted_buffer: List[str] = []
        self._last_injected_len: int = 0
        self._session_start_time: float = 0.0

        # Callbacks
        self.on_state_changed: Optional[Callable[[TyperState], None]] = None
        self.on_preview_update: Optional[Callable[[str, str], None]] = None  # (raw_text, annotated_html)
        self.on_committed: Optional[Callable[[str], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None

    @property
    def mode(self) -> TransformationMode:
        return self._mode

    @mode.setter
    def mode(self, new_mode: TransformationMode | str) -> None:
        if isinstance(new_mode, str):
            self._mode = TransformationMode(new_mode.lower())
        else:
            self._mode = new_mode

    @property
    def state(self) -> TyperState:
        return self._state

    def set_state(self, new_state: TyperState) -> None:
        if self._state != new_state:
            self._state = new_state
            if self.on_state_changed:
                try:
                    self.on_state_changed(new_state)
                except Exception as ex:
                    logger.warning("Error in on_state_changed callback: %s", ex)

    def start_session(self) -> None:
        """Begin a new voice typing session."""
        self._provisional_buffer = ""
        self._committed_text = ""
        self._retracted_buffer.clear()
        self._last_injected_len = 0
        self._session_start_time = time.time()
        self.set_state(TyperState.LISTENING)

    def handle_interim_hypothesis(self, hypothesis_text: str) -> Dict[str, Any]:
        """
        Process incoming real-time provisional hypothesis tokens.
        Detects verbal self-corrections dynamically and returns updated preview text and HTML.
        """
        if not hypothesis_text:
            return {"clean_text": "", "annotated_html": "", "has_corrections": False}

        # Apply vocabulary normalization first
        normalized = self.vocabulary_manager.normalize_text(hypothesis_text)

        # Detect verbal self-corrections and construct preview HTML with red strikethrough
        correction_result = DictationProcessor.detect_verbal_self_corrections(normalized)
        clean_text = correction_result["clean_text"]
        annotated_html = correction_result["annotated_html"]

        self._provisional_buffer = clean_text
        self._retracted_buffer = correction_result.get("retracted_segments", [])

        if self.on_preview_update:
            try:
                self.on_preview_update(clean_text, annotated_html)
            except Exception as ex:
                logger.warning("Error in on_preview_update callback: %s", ex)

        return correction_result

    def finalize_session(self, raw_utterance: Optional[str] = None) -> str:
        """
        Finalize speech dictation session:
        Applies self-correction, filler pruning (unless verbatim),
        mode-based transformations (Magic, Structured, Email), and commits text.
        """
        self.set_state(TyperState.PROCESSING)

        text_to_process = raw_utterance if raw_utterance is not None else self._provisional_buffer
        if not text_to_process.strip():
            self.set_state(TyperState.IDLE)
            return ""

        vocab_list = self.vocabulary_manager.list_terms(active_only=True)

        # Process through DictationProcessor with selected mode
        final_text = DictationProcessor.process(
            text_to_process,
            vocabulary=vocab_list,
            mode=self._mode.value,
        )

        self._committed_text = final_text
        self._last_injected_len = len(final_text)

        # Inject into active application
        try:
            self.text_inserter.insert_text(final_text)
            self.set_state(TyperState.COMMITTED)
            if self.on_committed:
                self.on_committed(final_text)
        except Exception as ex:
            logger.error("Text injection failed: %s", ex)
            self.set_state(TyperState.ERROR)
            if self.on_error:
                self.on_error(str(ex))

        return final_text

    def cancel_session(self) -> None:
        """Abort current session without text insertion."""
        self._provisional_buffer = ""
        self._retracted_buffer.clear()
        self.set_state(TyperState.IDLE)

    def undo_last_injection(self) -> bool:
        """Emit backspaces to undo the last injected character sequence."""
        if self._last_injected_len <= 0:
            return False
        try:
            self.text_inserter.send_backspaces(self._last_injected_len)
            self._last_injected_len = 0
            return True
        except Exception as ex:
            logger.warning("Undo failed: %s", ex)
            return False
