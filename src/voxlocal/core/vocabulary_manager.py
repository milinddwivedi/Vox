"""
Personal and Domain Vocabulary Manager for VOX.
Provides persistent storage, normalization, and contextual domain biasing
for technical terms, acronyms, company names, and Devanagari/Hindi loanwords.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Dict, List, Optional, Tuple


class VocabularyManager:
    """
    Manages personal, enterprise, and domain-specific vocabulary.
    Persists definitions to SQLite via LocalDatabase (or in-memory cache)
    and applies boundary-safe, case-insensitive normalization rules.
    """

    # Pre-seeded default domain vocabulary definitions
    DEFAULT_SEEDED_TERMS: List[Dict[str, Any]] = [
        # Tech & Product Names
        {"spoken_phrase": "vox local", "replacement": "VoxLocal", "category": "product"},
        {"spoken_phrase": "vox", "replacement": "VOX", "category": "product"},
        {"spoken_phrase": "antigravity", "replacement": "Antigravity", "category": "company"},
        {"spoken_phrase": "zepto", "replacement": "Zepto", "category": "company"},
        {"spoken_phrase": "vellora", "replacement": "Vellora", "category": "company"},
        {"spoken_phrase": "hubspot", "replacement": "HubSpot", "category": "tech"},
        {"spoken_phrase": "windchill", "replacement": "Windchill", "category": "tech"},
        {"spoken_phrase": "vs code", "replacement": "VS Code", "category": "tech"},
        {"spoken_phrase": "github", "replacement": "GitHub", "category": "tech"},
        {"spoken_phrase": "faster whisper", "replacement": "Faster-Whisper", "category": "tech"},
        {"spoken_phrase": "pyqt", "replacement": "PyQt6", "category": "tech"},
        {"spoken_phrase": "pyqt6", "replacement": "PyQt6", "category": "tech"},
        # Acronyms & Jargon
        {"spoken_phrase": "drhp", "replacement": "DRHP", "category": "business"},
        {"spoken_phrase": "d r h p", "replacement": "DRHP", "category": "business"},
        {"spoken_phrase": "cto", "replacement": "CTO", "category": "corporate"},
        {"spoken_phrase": "ceo", "replacement": "CEO", "category": "corporate"},
        {"spoken_phrase": "saas", "replacement": "SaaS", "category": "tech"},
        {"spoken_phrase": "b2b", "replacement": "B2B", "category": "business"},
        {"spoken_phrase": "b to b", "replacement": "B2B", "category": "business"},
        {"spoken_phrase": "llm", "replacement": "LLM", "category": "tech"},
        {"spoken_phrase": "rag", "replacement": "RAG", "category": "tech"},
        {"spoken_phrase": "mcp", "replacement": "MCP", "category": "tech"},
        {"spoken_phrase": "wasapi", "replacement": "WASAPI", "category": "audio"},
        {"spoken_phrase": "ci cd", "replacement": "CI/CD", "category": "tech"},
        {"spoken_phrase": "ci/cd", "replacement": "CI/CD", "category": "tech"},
        {"spoken_phrase": "api", "replacement": "API", "category": "tech"},
        {"spoken_phrase": "sql", "replacement": "SQL", "category": "tech"},
        {"spoken_phrase": "pr merge", "replacement": "PR merge", "category": "tech"},
        {"spoken_phrase": "pr review", "replacement": "PR review", "category": "tech"},
        # Devanagari & Hindi Loanwords
        {"spoken_phrase": "अपडेट", "replacement": "Update", "category": "hindi_loanword"},
        {"spoken_phrase": "लॉगिन", "replacement": "Login", "category": "hindi_loanword"},
        {"spoken_phrase": "रिशेड्यूल", "replacement": "Reschedule", "category": "hindi_loanword"},
        {"spoken_phrase": "मीटिंग", "replacement": "Meeting", "category": "hindi_loanword"},
        {"spoken_phrase": "प्रोजेक्ट", "replacement": "Project", "category": "hindi_loanword"},
        {"spoken_phrase": "क्लाइंट", "replacement": "Client", "category": "hindi_loanword"},
        {"spoken_phrase": "रिपोर्ट", "replacement": "Report", "category": "hindi_loanword"},
        {"spoken_phrase": "कंप्लीट", "replacement": "Complete", "category": "hindi_loanword"},
        {"spoken_phrase": "स्क्रीनशॉट", "replacement": "Screenshot", "category": "hindi_loanword"},
    ]

    def __init__(self, db: Optional[Any] = None) -> None:
        self.db = db
        self._lock = threading.RLock()
        self._memory_terms: Dict[str, Dict[str, Any]] = {}
        self._id_counter: int = 1
        self._init_defaults()

    def _init_defaults(self) -> None:
        """Seed default domain vocabulary into database or memory cache."""
        with self._lock:
            for item in self.DEFAULT_SEEDED_TERMS:
                self.add_term(
                    spoken_phrase=item["spoken_phrase"],
                    replacement=item["replacement"],
                    category=item.get("category", "general"),
                    is_active=True,
                )

    def add_term(
        self,
        spoken_phrase: str,
        replacement: str,
        category: str = "general",
        is_active: bool = True,
    ) -> int:
        """
        Add or update a vocabulary entry.
        Returns the integer ID of the vocabulary item.
        """
        phrase_clean = spoken_phrase.strip()
        repl_clean = replacement.strip()
        if not phrase_clean or not repl_clean:
            raise ValueError("Spoken phrase and replacement cannot be empty.")

        with self._lock:
            if self.db is not None and hasattr(self.db, "upsert_vocabulary"):
                try:
                    return int(
                        self.db.upsert_vocabulary(
                            phrase_clean,
                            repl_clean,
                            category=category,
                            is_active=is_active,
                        )
                    )
                except Exception:
                    pass

            # Memory fallback
            key = phrase_clean.lower()
            if key in self._memory_terms:
                item = self._memory_terms[key]
                item["replacement"] = repl_clean
                item["category"] = category
                item["is_active"] = 1 if is_active else 0
                return item["id"]
            else:
                tid = self._id_counter
                self._id_counter += 1
                self._memory_terms[key] = {
                    "id": tid,
                    "spoken_phrase": phrase_clean,
                    "replacement": repl_clean,
                    "category": category,
                    "is_active": 1 if is_active else 0,
                }
                return tid

    def remove_term(self, vocab_id: int) -> bool:
        """Remove a vocabulary item by ID."""
        with self._lock:
            if self.db is not None and hasattr(self.db, "delete_vocabulary"):
                try:
                    self.db.delete_vocabulary(vocab_id)
                except Exception:
                    pass

            for key, val in list(self._memory_terms.items()):
                if val.get("id") == vocab_id:
                    del self._memory_terms[key]
                    return True
            return False

    def remove_by_phrase(self, spoken_phrase: str) -> bool:
        """Remove a vocabulary item by spoken phrase."""
        with self._lock:
            key = spoken_phrase.strip().lower()
            if key in self._memory_terms:
                tid = self._memory_terms[key]["id"]
                if self.db is not None and hasattr(self.db, "delete_vocabulary"):
                    try:
                        self.db.delete_vocabulary(tid)
                    except Exception:
                        pass
                del self._memory_terms[key]
                return True
            return False

    def get_term(self, vocab_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a specific term by ID."""
        with self._lock:
            for item in self.list_terms(active_only=False):
                if item.get("id") == vocab_id:
                    return dict(item)
            return None

    def list_terms(
        self,
        category: Optional[str] = None,
        active_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """List all vocabulary terms, optionally filtered by category and active status."""
        with self._lock:
            items: List[Dict[str, Any]] = []
            if self.db is not None and hasattr(self.db, "list_vocabulary"):
                try:
                    db_items = self.db.list_vocabulary(active_only=active_only)
                    if db_items:
                        items = [dict(r) for r in db_items]
                except Exception:
                    items = []

            if not items:
                items = list(self._memory_terms.values())

            results: List[Dict[str, Any]] = []
            for it in items:
                if active_only and not it.get("is_active", 1):
                    continue
                if category and it.get("category") != category:
                    continue
                results.append(dict(it))

            return sorted(results, key=lambda x: str(x.get("spoken_phrase", "")).lower())

    def update_term(
        self,
        vocab_id: int,
        replacement: Optional[str] = None,
        is_active: Optional[bool] = None,
        category: Optional[str] = None,
    ) -> bool:
        """Update fields of an existing term."""
        with self._lock:
            term = self.get_term(vocab_id)
            if not term:
                return False
            phrase = term["spoken_phrase"]
            repl = replacement if replacement is not None else term["replacement"]
            active = is_active if is_active is not None else bool(term.get("is_active", 1))
            cat = category if category is not None else term.get("category", "general")
            self.add_term(phrase, repl, category=cat, is_active=active)
            return True

    def clear_terms(self) -> None:
        """Clear all terms from cache."""
        with self._lock:
            self._memory_terms.clear()

    def import_terms(self, terms: List[Dict[str, Any]]) -> int:
        """Bulk import a list of vocabulary term dicts. Returns count added."""
        count = 0
        with self._lock:
            for item in terms:
                phrase = item.get("spoken_phrase")
                repl = item.get("replacement")
                if phrase and repl:
                    self.add_term(
                        spoken_phrase=str(phrase),
                        replacement=str(repl),
                        category=item.get("category", "general"),
                        is_active=bool(item.get("is_active", True)),
                    )
                    count += 1
        return count

    def export_terms(self) -> List[Dict[str, Any]]:
        """Export all vocabulary terms as a dictionary list."""
        return self.list_terms(active_only=False)

    def normalize_text(self, text: str) -> str:
        """
        Apply active custom vocabulary replacements to text.
        Substitutions are applied in descending order of phrase length (longest first)
        using word-boundary matching and case-insensitivity.
        """
        if not text or not text.strip():
            return text

        terms = self.list_terms(active_only=True)
        if not terms:
            return text

        # Sort descending by phrase length so multi-word compounds match before individual words
        sorted_terms = sorted(
            terms,
            key=lambda item: len(str(item.get("spoken_phrase", ""))),
            reverse=True,
        )

        result = text
        for item in sorted_terms:
            spoken = str(item.get("spoken_phrase", "")).strip()
            repl = str(item.get("replacement", "")).strip()
            if not spoken or not repl:
                continue

            # Boundary-safe regex: handles both ASCII words and Unicode (e.g. Devanagari)
            if re.match(r"^[\w\s]+$", spoken, flags=re.UNICODE):
                pattern = rf"(?<!\w){re.escape(spoken)}(?!\w)"
            else:
                pattern = rf"(?:^|\s|\b){re.escape(spoken)}(?:$|\s|\b|[.,!?;:])"

            result = re.sub(pattern, repl, result, flags=re.IGNORECASE)

        return result

    def harvest_from_context(
        self,
        window_title: str = "",
        attendees: Optional[List[str]] = None,
    ) -> List[str]:
        """
        Extract domain terms from window titles and meeting attendee names
        for real-time dynamic vocabulary biasing.
        """
        harvested: List[str] = []
        if window_title:
            cleaned = re.sub(r"[\\/_\-\|\:\.\(\)\[\]]", " ", window_title)
            tokens = [t.strip() for t in cleaned.split() if len(t.strip()) >= 3]
            stop = {"microsoft", "google", "chrome", "edge", "teams", "zoom", "meet", "untitled", "window", "slack"}
            for tok in tokens:
                if tok.lower() not in stop and not tok.isdigit():
                    if tok not in harvested:
                        harvested.append(tok)
                        # Auto-register as transient domain term if not existing
                        self.add_term(tok, tok, category="contextual")

        if attendees:
            for att in attendees:
                clean_att = att.strip()
                if clean_att and clean_att not in harvested:
                    harvested.append(clean_att)
                    self.add_term(clean_att, clean_att, category="attendee")

        return harvested
