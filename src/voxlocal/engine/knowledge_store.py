"""
VOX Local Retrieval-Based Knowledge Retention & Learning System.

Combines:
1. Local quantized ONNX dense embeddings (`Qdrant/bge-small-en-v1.5-onnx-Q`, 384-d L2-normalized)
   using already-bundled `onnxruntime` + `tokenizers`.
2. Persistent SQLite FTS5 + vector `rag_chunks` index (rebuildable derived index).
3. Hybrid BM25 + Semantic Reciprocal Rank Fusion (RRF) with cross-meeting diversity.
4. Typed Knowledge Store (`entity`, `acronym`, `glossary`, `participant`, `project`, `fact`,
   `decision`, `open_issue`) with source provenance, deduplication, and dated conflict tracking.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from voxlocal.core.logger import get_app_root, get_logger, get_user_models_root
from voxlocal.engine.meeting_model_manager import MeetingModelManager
from voxlocal.storage.exporter import format_seconds_hms

logger = get_logger("engine.knowledge_store")

EMBEDDING_DIM = 384


def _pack_embedding(vec: np.ndarray) -> bytes:
    arr = np.asarray(vec, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(arr))
    if norm > 1e-8:
        arr = arr / norm
    return arr.astype(np.float32).tobytes()


def _unpack_embedding(blob: Optional[bytes]) -> Optional[np.ndarray]:
    if not blob:
        return None
    arr = np.frombuffer(blob, dtype=np.float32)
    if arr.size != EMBEDDING_DIM:
        return None
    return arr


def _build_chunk_windows(
    segments: List[Dict[str, Any]],
    *,
    target_words: int = 240,
    overlap_segments: int = 2,
) -> List[Dict[str, Any]]:
    """
    Build chronological, speaker-attributed transcript windows suitable for hybrid RAG.
    Preserves [MM:SS] timestamps and distinct speakers per chunk.
    """
    cleaned = [s for s in (segments or []) if str(s.get("text") or "").strip()]
    if not cleaned:
        return []

    chunks: List[Dict[str, Any]] = []
    n = len(cleaned)
    i = 0
    while i < n:
        cur_lines: List[str] = []
        cur_speakers: List[str] = []
        seen_spk: set[str] = set()
        word_cnt = 0
        j = i
        start_t = float(cleaned[i].get("start_time") or 0.0)
        end_t = start_t
        while j < n:
            seg = cleaned[j]
            txt = str(seg.get("text") or "").strip()
            spk = str(seg.get("speaker") or "Speaker").strip()
            st = float(seg.get("start_time") or 0.0)
            et = float(seg.get("end_time") or st)
            mm, ss = divmod(max(0, int(st)), 60)
            cur_lines.append(f"[{mm:02d}:{ss:02d}|{spk}] {txt}")
            if spk not in seen_spk:
                seen_spk.add(spk)
                cur_speakers.append(spk)
            word_cnt += len(txt.split())
            end_t = max(end_t, et)
            j += 1
            if word_cnt >= target_words:
                break

        chunks.append(
            {
                "chunk_index": len(chunks),
                "start_time": round(start_t, 2),
                "end_time": round(end_t, 2),
                "speakers": cur_speakers,
                "text": "\n".join(cur_lines),
            }
        )
        if j >= n:
            break
        step_back = min(overlap_segments, max(0, (j - i) - 1))
        i = max(i + 1, j - step_back)

    return chunks


class LocalOnnxEmbedder:
    """Lazy-loaded local ONNX sentence embedder (`bge-small-en-v1.5-onnx-Q`)."""

    MODEL_ID = "bge-small-en-v1.5-onnx-Q"

    def __init__(self, models_dir: Optional[str | Path] = None) -> None:
        self.models_dir = Path(models_dir) if models_dir else get_user_models_root()
        self.manager = MeetingModelManager(self.models_dir)
        self._lock = threading.RLock()
        self._sess: Optional[Any] = None
        self._tok: Optional[Any] = None
        self._input_names: List[str] = []

    def is_available(self) -> bool:
        return bool(self.manager.get_artifact_status("meeting_embeddings").get("installed"))

    def _ensure_loaded(self) -> bool:
        with self._lock:
            if self._sess is not None and self._tok is not None:
                return True
            if not self.is_available():
                return False
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                emb_dir = self.manager.get_artifact_dir("meeting_embeddings")
                tok = Tokenizer.from_file(str(emb_dir / "tokenizer.json"))
                tok.enable_truncation(max_length=512)
                tok.enable_padding(length=None)
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = 2
                sess = ort.InferenceSession(
                    str(emb_dir / "model_optimized.onnx"),
                    sess_options=opts,
                    providers=["CPUExecutionProvider"],
                )
                self._tok = tok
                self._sess = sess
                self._input_names = [inp.name for inp in sess.get_inputs()]
                return True
            except Exception as exc:
                logger.warning("Failed initializing local ONNX embedder: %s", exc)
                self._sess = None
                self._tok = None
                return False

    def unload(self) -> None:
        with self._lock:
            self._sess = None
            self._tok = None

    def embed_texts(self, texts: List[str], batch_size: int = 16) -> Optional[np.ndarray]:
        if not texts:
            return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        if not self._ensure_loaded():
            return None

        all_vecs: List[np.ndarray] = []
        with self._lock:
            for start in range(0, len(texts), batch_size):
                batch = [str(t or "")[:2000] for t in texts[start : start + batch_size]]
                encs = self._tok.encode_batch(batch)
                ids = np.array([e.ids for e in encs], dtype=np.int64)
                mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
                feeds: Dict[str, Any] = {
                    "input_ids": ids,
                    "attention_mask": mask,
                }
                if "token_type_ids" in self._input_names:
                    feeds["token_type_ids"] = np.array([e.type_ids for e in encs], dtype=np.int64)
                out = self._sess.run(None, feeds)[0]
                if out.ndim == 3:
                    # CLS pooling (BGE standard)
                    vecs = out[:, 0, :].astype(np.float32)
                else:
                    vecs = out.astype(np.float32)
                norms = np.linalg.norm(vecs, axis=1, keepdims=True)
                vecs = vecs / np.maximum(norms, 1e-8)
                all_vecs.append(vecs)
        return np.vstack(all_vecs) if all_vecs else np.zeros((0, EMBEDDING_DIM), dtype=np.float32)


class LocalKnowledgeStore:
    """
    Hybrid BM25 + Semantic RAG Index & Persistent Cross-Meeting Knowledge Manager.
    """

    def __init__(self, db: Any, models_dir: Optional[str | Path] = None) -> None:
        self.db = db
        self.embedder = LocalOnnxEmbedder(models_dir=models_dir)
        self._index_lock = threading.RLock()

    def index_meeting(self, meeting_id: int) -> int:
        """Build/refresh overlapping transcript windows + embeddings for one meeting."""
        with self._index_lock:
            meeting = self.db.get_meeting(meeting_id)
            if not meeting:
                return 0
            segs = meeting.get("segments") or self.db.get_meeting_segments(meeting_id)
            if not segs:
                return 0
            windows = _build_chunk_windows(segs)
            if not windows:
                return 0

            emb_matrix = self.embedder.embed_texts([w["text"] for w in windows])
            emb_model_name = self.embedder.MODEL_ID if emb_matrix is not None else ""
            for idx, w in enumerate(windows):
                if emb_matrix is not None and idx < len(emb_matrix):
                    w["embedding_blob"] = _pack_embedding(emb_matrix[idx])
                    w["embedding_model"] = emb_model_name
                else:
                    w["embedding_blob"] = None
                    w["embedding_model"] = ""

            self.db.replace_meeting_rag_chunks(
                meeting_id=int(meeting_id),
                meeting_title=str(meeting.get("title") or f"Meeting #{meeting_id}"),
                chunks=windows,
            )
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.db.update_meeting(int(meeting_id), knowledge_indexed_at=now_str)
            return len(windows)

    def ensure_meetings_indexed(self, meeting_ids: Optional[List[int]] = None, limit: int = 30) -> int:
        """Lazily index unindexed meetings that already have transcript segments."""
        if not hasattr(self.db, "list_unindexed_meeting_ids"):
            return 0
        pending_ids = self.db.list_unindexed_meeting_ids(meeting_ids=meeting_ids, limit=limit)
        indexed_cnt = 0
        for mid in pending_ids:
            try:
                if self.index_meeting(int(mid)) > 0:
                    indexed_cnt += 1
            except Exception as exc:
                logger.warning("Failed indexing meeting #%s: %s", mid, exc)
        return indexed_cnt

    def rebuild_all_indexes(
        self,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """Rebuild derived RAG chunks & FTS5 index across all stored meetings cleanly."""
        meetings = self.db.list_meetings(limit=500)
        total = len(meetings)
        indexed_meetings = 0
        total_chunks = 0
        for i, m in enumerate(meetings, 1):
            mid = int(m["id"])
            title = str(m.get("title") or f"Meeting #{mid}")
            if progress_callback:
                progress_callback(i, total, f"Indexing {title}...")
            n = self.index_meeting(mid)
            if n > 0:
                indexed_meetings += 1
                total_chunks += n
        return {
            "meetings_scanned": total,
            "meetings_indexed": indexed_meetings,
            "total_chunks": total_chunks,
            "embeddings_enabled": self.embedder.is_available(),
        }

    def ingest_meeting_knowledge(
        self,
        meeting_id: int,
        items: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        """
        Persist extracted knowledge items with deduplication, source provenance,
        and dated conflict preservation without overwriting user edits.
        """
        if not items or not hasattr(self.db, "upsert_knowledge_item"):
            return {"inserted": 0, "updated": 0, "conflicted": 0}
        meeting = self.db.get_meeting(meeting_id) or {}
        m_title = str(meeting.get("title") or f"Meeting #{meeting_id}")
        m_date = str(meeting.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        counts = {"inserted": 0, "updated": 0, "conflicted": 0}
        for raw in items:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            content = str(raw.get("content") or "").strip()
            if not title or not content:
                continue
            item_type = str(raw.get("item_type") or "fact").strip().lower()
            if item_type not in ("entity", "acronym", "glossary", "participant", "project", "fact", "decision", "open_issue"):
                item_type = "fact"
            canonical_key = re.sub(r"\s+", " ", str(raw.get("canonical_key") or title).strip().lower())
            ts_raw = raw.get("timestamp_sec")
            if ts_raw is None and raw.get("timestamp_str"):
                ts_raw = self._parse_ts_str(str(raw.get("timestamp_str")))
            ts_sec = float(ts_raw or 0.0)
            aliases = raw.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [a.strip() for a in aliases.split(",") if a.strip()]
            subcat = str(raw.get("subcategory") or "").strip().lower()
            entity_name = str(raw.get("entity_name") or "").strip()
            meta = {"aliases": aliases}
            if subcat:
                meta["subcategory"] = subcat
            res = self.db.upsert_knowledge_item(
                item_type=item_type,
                canonical_key=canonical_key,
                title=title,
                content=content,
                metadata=meta,
                confidence=float(raw.get("confidence", 0.85)),
                valid_from_date=m_date[:10],
                source_meeting_id=int(meeting_id),
                source_meeting_title=m_title,
                source_meeting_date=m_date,
                source_speaker=str(raw.get("speaker") or ""),
                source_timestamp_sec=ts_sec,
                source_evidence_quote=str(raw.get("evidence_quote") or "")[:300],
                entity_name=entity_name,
            )
            op = res.get("action")
            if op in counts:
                counts[op] += 1
        return counts

    @staticmethod
    def _parse_ts_str(ts_str: str) -> float:
        parts = [p for p in str(ts_str or "").strip().split(":") if p.isdigit()]
        if len(parts) == 2:
            return float(int(parts[0]) * 60 + int(parts[1]))
        if len(parts) == 3:
            return float(int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]))
        return 0.0

    def get_meeting_glossary_rules(self) -> List[Dict[str, Any]]:
        """Return active glossary/acronym/participant items safe for transcript normalization."""
        if not hasattr(self.db, "list_knowledge_items"):
            return []
        out: List[Dict[str, Any]] = []
        for t in ("glossary", "acronym", "entity", "participant"):
            out.extend(self.db.list_knowledge_items(item_type=t, include_superseded=False, limit=200))
        return out

    def _resolve_meeting_aliases(self, meeting_id: int) -> Dict[str, str]:
        if not hasattr(self.db, "get_speaker_aliases"):
            return {}
        try:
            aliases = self.db.get_speaker_aliases(int(meeting_id)) or {}
            # Collapse transitive alias chains if any
            resolved: Dict[str, str] = {}
            for raw_lbl, disp in aliases.items():
                cur = str(disp or "").strip()
                seen = {str(raw_lbl)}
                while cur in aliases and cur not in seen:
                    seen.add(cur)
                    cur = str(aliases[cur] or "").strip()
                if raw_lbl and cur:
                    resolved[str(raw_lbl)] = cur
            return resolved
        except Exception:
            return {}

    @staticmethod
    def _apply_aliases_to_chunk_text(text: str, speakers: List[str], aliases: Dict[str, str]) -> Tuple[str, List[str]]:
        if not aliases:
            return text, speakers
        out_txt = str(text or "")
        for raw_lbl, disp in aliases.items():
            if raw_lbl and disp and raw_lbl != disp:
                out_txt = out_txt.replace(f"|{raw_lbl}]", f"|{disp}]")
        out_spks: List[str] = []
        seen: set[str] = set()
        for sp in speakers or []:
            mapped = aliases.get(str(sp), str(sp)).strip()
            if mapped and mapped not in seen:
                seen.add(mapped)
                out_spks.append(mapped)
        return out_txt, out_spks

    def extract_and_index_meeting(self, meeting_id: int, llm_engine: Optional[Any] = None) -> Dict[str, Any]:
        """Index transcript chunks and optionally extract knowledge for a meeting lacking knowledge items."""
        chunks_indexed = self.index_meeting(int(meeting_id))
        extracted = 0
        ingested: Dict[str, int] = {"inserted": 0, "updated": 0, "conflicted": 0}
        if llm_engine is not None and getattr(llm_engine, "is_available", lambda: False)() and hasattr(llm_engine, "extract_meeting_knowledge"):
            m = self.db.get_meeting(int(meeting_id)) or {}
            segs = m.get("segments") or self.db.get_meeting_segments(int(meeting_id))
            if segs:
                k_items = llm_engine.extract_meeting_knowledge(
                    segs,
                    title=str(m.get("title") or f"Meeting #{meeting_id}"),
                    meeting_date=str(m.get("created_at") or "")[:10],
                )
                extracted = len(k_items or [])
                if k_items:
                    ingested = self.ingest_meeting_knowledge(int(meeting_id), k_items)
        return {
            "meeting_id": int(meeting_id),
            "chunks_indexed": chunks_indexed,
            "extracted": extracted,
            **ingested,
        }

    def hybrid_search(
        self,
        query: str,
        *,
        meeting_id: Optional[int] = None,
        meeting_ids: Optional[List[int]] = None,
        top_k: int = 12,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Execute hybrid FTS5 BM25 + BGE semantic vector RRF search across indexed chunks
        and retrieve relevant typed knowledge items within strict meeting scope.
        """
        q = str(query or "").strip()
        if not q:
            return [], []
        allowed_ids: Optional[set[int]] = None
        if meeting_id is None and meeting_ids is not None:
            allowed_ids = {int(x) for x in meeting_ids}
            if not allowed_ids:
                return [], []

        if meeting_id is not None:
            self.ensure_meetings_indexed(meeting_ids=[int(meeting_id)], limit=1)
        elif allowed_ids is not None:
            self.ensure_meetings_indexed(meeting_ids=list(allowed_ids)[:15], limit=15)
        else:
            self.ensure_meetings_indexed(limit=15)

        q_tokens = {
            w.lower()
            for w in re.findall(r"[a-zA-Z0-9_-]{2,}", q)
            if w.lower() not in {"what", "when", "where", "which", "about", "were", "with", "from", "that", "this", "have", "does", "did", "said", "talked"}
        }

        # 1. BM25 / FTS5 lexical candidates
        fts_hits = self.db.search_rag_fts(q, meeting_id=meeting_id, limit=50) if hasattr(self.db, "search_rag_fts") else []
        if allowed_ids is not None:
            fts_hits = [r for r in fts_hits if int(r.get("meeting_id") or 0) in allowed_ids]

        # 2. Vector semantic similarity across candidate chunks
        all_chunks = self.db.list_rag_chunks(meeting_id=meeting_id) if hasattr(self.db, "list_rag_chunks") else []
        if allowed_ids is not None:
            all_chunks = [r for r in all_chunks if int(r.get("meeting_id") or 0) in allowed_ids]
        vec_hits: List[Tuple[int, float]] = []
        if all_chunks:
            q_vec_mat = self.embedder.embed_texts([q])
            if q_vec_mat is not None and q_vec_mat.shape[0] == 1:
                q_vec = q_vec_mat[0]
                scored_vecs: List[Tuple[int, float]] = []
                for row in all_chunks:
                    v = _unpack_embedding(row.get("embedding_blob"))
                    if v is not None:
                        sim = float(np.dot(q_vec, v))
                        scored_vecs.append((int(row["id"]), sim))
                scored_vecs.sort(key=lambda kv: -kv[1])
                vec_hits = scored_vecs[:50]

        # 3. Reciprocal Rank Fusion (RRF) + lexical keyword bonus
        chunk_by_id: Dict[int, Dict[str, Any]] = {int(r["id"]): r for r in all_chunks}
        rrf_scores: Dict[int, float] = {}
        vec_sim_map: Dict[int, float] = {cid: sim for cid, sim in vec_hits}
        fts_rank_map: Dict[int, int] = {}

        for rank, row in enumerate(fts_hits, 1):
            cid = int(row["chunk_id"])
            if cid not in chunk_by_id:
                chunk_by_id[cid] = row
            fts_rank_map[cid] = rank
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (50.0 + rank))

        for rank, (cid, sim) in enumerate(vec_hits, 1):
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (50.0 + rank)) + max(0.0, sim) * 0.02

        # Include lexical exact token overlap if FTS yielded few hits
        for cid, row in chunk_by_id.items():
            txt_low = str(row.get("text") or "").lower()
            ov = sum(1 for tok in q_tokens if tok in txt_low) if q_tokens else 0
            if ov > 0:
                rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 0.015 * ov

        ranked_cids = sorted(rrf_scores.keys(), key=lambda cid: -rrf_scores[cid])

        # Enforce cross-meeting diversity when querying across all meetings (max 4 chunks per meeting)
        selected: List[Dict[str, Any]] = []
        per_meeting_cnt: Dict[int, int] = {}
        max_per_meeting = top_k if meeting_id is not None else max(3, min(4, top_k // 2))
        alias_cache: Dict[int, Dict[str, str]] = {}

        for cid in ranked_cids:
            row = chunk_by_id.get(cid)
            if not row:
                continue
            mid = int(row.get("meeting_id") or 0)
            if per_meeting_cnt.get(mid, 0) >= max_per_meeting:
                continue
            per_meeting_cnt[mid] = per_meeting_cnt.get(mid, 0) + 1

            if mid not in alias_cache:
                alias_cache[mid] = self._resolve_meeting_aliases(mid)
            aliases = alias_cache[mid]

            spk_raw = row.get("speakers_json") or "[]"
            try:
                spks = json.loads(spk_raw) if isinstance(spk_raw, str) else list(spk_raw)
            except Exception:
                spks = []
            txt = str(row.get("text") or "")
            txt, spks = self._apply_aliases_to_chunk_text(txt, [str(s) for s in spks], aliases)
            txt_low = txt.lower()
            lex_hits = sum(1 for tok in q_tokens if tok in txt_low)
            sim_val = float(vec_sim_map.get(cid, 0.0))

            selected.append(
                {
                    "chunk_id": cid,
                    "meeting_id": mid,
                    "meeting_title": str(row.get("meeting_title") or f"Meeting #{mid}"),
                    "meeting_date": str(row.get("meeting_date") or ""),
                    "timestamp_sec": float(row.get("start_time") or 0.0),
                    "end_time_sec": float(row.get("end_time") or 0.0),
                    "timestamp_formatted": format_seconds_hms(float(row.get("start_time") or 0.0)),
                    "speaker": " / ".join(str(s) for s in spks[:4]) or "Meeting",
                    "speakers": spks[:4],
                    "text": txt,
                    "quote": txt,
                    "rrf_score": round(rrf_scores[cid], 5),
                    "semantic_sim": round(sim_val, 4),
                    "lexical_hits": lex_hits,
                    "source": "hybrid_rag",
                }
            )
            if len(selected) >= top_k:
                break

        # Also query persistent knowledge items matching query tokens within strict scope
        matched_knowledge: List[Dict[str, Any]] = []
        if hasattr(self.db, "list_knowledge_items"):
            try:
                all_k = self.db.list_knowledge_items(
                    include_superseded=False,
                    limit=250,
                    meeting_id=meeting_id,
                    meeting_ids=list(allowed_ids) if allowed_ids is not None else None,
                )
            except TypeError:
                all_k = self.db.list_knowledge_items(include_superseded=False, limit=250)
                if meeting_id is not None:
                    all_k = [ki for ki in all_k if any(int(s.get("meeting_id") or 0) == int(meeting_id) for s in (ki.get("sources") or []))]
                elif allowed_ids is not None:
                    all_k = [ki for ki in all_k if any(int(s.get("meeting_id") or 0) in allowed_ids for s in (ki.get("sources") or []))]
            scored_k: List[Tuple[float, Dict[str, Any]]] = []
            for ki in all_k:
                hay = f"{ki.get('title', '')} {ki.get('content', '')}".lower()
                hits = sum(1 for tok in q_tokens if tok in hay)
                if hits > 0:
                    ki_copy = dict(ki)
                    for src in ki_copy.get("sources") or []:
                        smid = src.get("meeting_id")
                        if smid is not None:
                            if int(smid) not in alias_cache:
                                alias_cache[int(smid)] = self._resolve_meeting_aliases(int(smid))
                            sp = str(src.get("speaker") or "")
                            if sp in alias_cache[int(smid)]:
                                src["speaker"] = alias_cache[int(smid)][sp]
                    scored_k.append((float(hits), ki_copy))
            scored_k.sort(key=lambda kv: -kv[0])
            matched_knowledge = [kv[1] for kv in scored_k[:8]]

        return selected, matched_knowledge
