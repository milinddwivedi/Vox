"""
Strict Verified-Only Ground-Truth Scorer for VOX Speech Intelligence.

HARD GATE: Never computes WER/CER or speaker metrics against unverified model drafts.
Requires `verified_by_human == True` on reference segments; if zero segments are
human-verified, returns `status: "awaiting_human_verification"` with None metrics.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from voxlocal.engine.dictation_processor import DictationProcessor

import numpy as np
from scipy.optimize import linear_sum_assignment

UNCERTAIN_SPAN_RE = re.compile(r"\[\?(.*?)\?\]")
PUNCT_STRIP_RE = re.compile(r"[^\w\s]", re.UNICODE)
_DEVA_CHAR_RE = re.compile(r"[\u0900-\u097F]")
_ARAB_CHAR_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_CJK_CHAR_RE = re.compile(r"[\u4E00-\u9FFF\u3400-\u4DBF\u3000-\u303F\uFF00-\uFFEF]")
_KANA_HANGUL_CYR_RE = re.compile(r"[\u3040-\u30FF\uAC00-\uD7AF\u0400-\u04FF]")
_LATIN_WORD_RE = re.compile(r"\b[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*\b")
_DEVA_WORD_RE = re.compile(r"[\u0900-\u097F]+")

DEFAULT_TECHNICAL_ENTITIES: Tuple[str, ...] = (
    "WFX",
    "PLM",
    "ERP",
    "HubSpot",
    "VOX",
    "Teams",
    "Postman",
    "UPC",
    "Remove Programs",
    "Daily Scrum",
    "Account Management",
    "Vikas",
    "Abhilash",
    "Hemanth",
    "Rumjhum",
    "Excel",
    "Zoom",
    "Windows",
    "System Tray",
    "Settings",
)


def parse_reference_text(text: str) -> Tuple[List[Tuple[str, bool]], int]:
    """
    Parse reference text containing optional `[?uncertain?]` markers into a sequence
    of `(token, is_uncertain)` pairs and total count of tokens inside uncertain markers.
    """
    tokens: List[Tuple[str, bool]] = []
    uncertain_count = 0
    pos = 0
    src = str(text or "")
    for m in UNCERTAIN_SPAN_RE.finditer(src):
        before = src[pos : m.start()]
        for w in before.split():
            tokens.append((w, False))
        inner = m.group(1) or ""
        inner_words = [w for w in inner.split() if w.strip()]
        if inner_words:
            for w in inner_words:
                tokens.append((w, True))
                uncertain_count += 1
        else:
            tokens.append(("", True))
            uncertain_count += 1
        pos = m.end()
    tail = src[pos:]
    for w in tail.split():
        tokens.append((w, False))
    return tokens, uncertain_count


def normalize_strict_text(text: str) -> str:
    s = PUNCT_STRIP_RE.sub(" ", str(text or "").lower())
    return " ".join(s.split())


def normalize_hinglish_token(token: str) -> str:
    w = PUNCT_STRIP_RE.sub("", str(token or "").lower()).strip()
    if not w:
        return ""
    if w in ("mein", "me", "mai"):
        return "me"
    if w in ("nahi", "nahin", "nhi", "nai"):
        return "nahi"
    if w in ("hain", "hai"):
        return "hai"
    if w in ("woh", "voh", "wo", "vo"):
        return "vo"
    if w in ("yeh", "ye"):
        return "ye"
    if w in ("theek", "thek", "thik"):
        return "thik"
    if w in ("achha", "accha", "aacha", "acha"):
        return "acha"
    if w in ("raha", "rha", "raa"):
        return "raha"
    if w in ("kar", "kr"):
        return "kar"
    if w in ("hoon", "hun", "hu"):
        return "hu"
    if w in ("okay", "ok"):
        return "ok"
    # Phonetic Roman-Hinglish vowel/consonant folding
    w = w.replace("ph", "f").replace("w", "v").replace("aa", "a").replace("ee", "i").replace("ii", "i").replace("oo", "u").replace("uu", "u")
    return w


def normalize_hinglish(text: str) -> str:
    """
    Orthography-aware normalization for Hinglish text.
    Converts Devanagari Hindi or stray Perso-Arabic script into Romanized Hinglish
    via DictationProcessor while leaving existing Latin/English tokens intact.
    """
    src = str(text or "")
    if not src:
        return ""
    if _DEVA_CHAR_RE.search(src) or _ARAB_CHAR_RE.search(src):
        src = DictationProcessor.devanagari_to_roman_hinglish(src)
    return src


def tokenize_strict(text: str) -> List[str]:
    clean = normalize_strict_text(text)
    return [t for t in clean.split() if t]


def tokenize_hinglish_norm(text: str) -> List[str]:
    out: List[str] = []
    for tok in tokenize_strict(normalize_hinglish(text)):
        nt = normalize_hinglish_token(tok)
        if nt:
            out.append(nt)
    return out


def audit_mixed_script_and_utf8(segments: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """
    Count Devanagari words, Latin words, foreign-script leaks, and U+FFFD replacement
    corruption across raw/primary text segments.
    """
    devanagari_words = 0
    latin_words = 0
    cjk_segs = 0
    arabic_segs = 0
    kana_hangul_cyrillic_segs = 0
    ufffd_segs = 0
    total_words = 0
    for seg in segments:
        txt = str(seg.get("raw_text") or seg.get("text") or "")
        if not txt:
            continue
        devanagari_words += len(_DEVA_WORD_RE.findall(txt))
        latin_words += len(_LATIN_WORD_RE.findall(txt))
        total_words += len(txt.split())
        if _CJK_CHAR_RE.search(txt):
            cjk_segs += 1
        if _ARAB_CHAR_RE.search(txt):
            arabic_segs += 1
        if _KANA_HANGUL_CYR_RE.search(txt):
            kana_hangul_cyrillic_segs += 1
        if "\ufffd" in txt:
            ufffd_segs += 1
    return {
        "devanagari_words": devanagari_words,
        "latin_words": latin_words,
        "cjk_segs": cjk_segs,
        "arabic_segs": arabic_segs,
        "kana_hangul_cyrillic_segs": kana_hangul_cyrillic_segs,
        "ufffd_segs": ufffd_segs,
        "total_words": total_words,
    }


def levenshtein_distance(a: Sequence[str], b: Sequence[str]) -> int:
    m, n = len(a), len(b)
    if m == 0:
        return n
    if n == 0:
        return m
    prev = list(range(n + 1))
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        curr[0] = i
        ai = a[i - 1]
        for j in range(1, n + 1):
            cost = 0 if ai == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[n]


def levenshtein_ops(a: Sequence[str], b: Sequence[str]) -> Tuple[int, int, int, int]:
    """Return `(edits, substitutions, deletions, insertions)` via deterministic DP backtrace."""
    m, n = len(a), len(b)
    if m == 0:
        return n, 0, 0, n
    if n == 0:
        return m, 0, m, 0
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        dp[i][0] = i
    for j in range(1, n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        ai = a[i - 1]
        for j in range(1, n + 1):
            cost = 0 if ai == b[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)

    subs = dels = ins = 0
    i, j = m, n
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            cost = 0 if a[i - 1] == b[j - 1] else 1
            if dp[i][j] == dp[i - 1][j - 1] + cost:
                if cost:
                    subs += 1
                i -= 1
                j -= 1
                continue
        if i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            dels += 1
            i -= 1
            continue
        ins += 1
        j -= 1
    return int(dp[m][n]), subs, dels, ins


def align_tokens_and_ops(
    ref_raw: str,
    pred_raw: str,
    *,
    hinglish_norm: bool = False,
    mask_uncertain: bool = True,
) -> Tuple[int, int, List[str], List[str], int, int, int]:
    """
    Align reference tokens against predicted tokens with optional masking of `[?...?]` slots.
    Returns `(edits, ref_token_count, kept_ref, kept_pred, substitutions, deletions, insertions)`.
    """
    tok_fn = tokenize_hinglish_norm if hinglish_norm else tokenize_strict
    pred_toks = tok_fn(pred_raw)

    if not mask_uncertain:
        unwrapped_ref = UNCERTAIN_SPAN_RE.sub(r"\1", str(ref_raw or ""))
        ref_toks = tok_fn(unwrapped_ref)
        edits, subs, dels, ins = levenshtein_ops(ref_toks, pred_toks)
        return edits, len(ref_toks), list(ref_toks), list(pred_toks), subs, dels, ins

    parsed, _ = parse_reference_text(ref_raw)
    ref_seq: List[Tuple[str, bool]] = []
    certain_count = 0
    for raw_tok, is_unc in parsed:
        if is_unc:
            norm_toks = tok_fn(raw_tok) if raw_tok else [""]
            for nt in (norm_toks or [""]):
                ref_seq.append((nt, True))
        else:
            for nt in tok_fn(raw_tok):
                ref_seq.append((nt, False))
                certain_count += 1

    m, n = len(ref_seq), len(pred_toks)
    if m == 0:
        return len(pred_toks), 0, [], list(pred_toks), 0, 0, len(pred_toks)

    if not any(is_unc for _, is_unc in ref_seq):
        plain_ref = [t for t, _ in ref_seq]
        edits, subs, dels, ins = levenshtein_ops(plain_ref, pred_toks)
        return edits, len(plain_ref), plain_ref, list(pred_toks), subs, dels, ins

    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for j in range(1, n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        _, is_unc = ref_seq[i - 1]
        dp[i][0] = dp[i - 1][0] if is_unc else (dp[i - 1][0] + 1)
        for j in range(1, n + 1):
            if is_unc:
                dp[i][j] = min(dp[i - 1][j], dp[i - 1][j - 1], dp[i][j - 1] + 1)
            else:
                sub_cost = 0 if ref_seq[i - 1][0] == pred_toks[j - 1] else 1
                dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + sub_cost)

    kept_ref_rev: List[str] = []
    kept_pred_rev: List[str] = []
    subs = dels = ins = 0
    i, j = m, n
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            rtok, is_unc = ref_seq[i - 1]
            if is_unc and dp[i][j] == dp[i - 1][j - 1]:
                i -= 1
                j -= 1
                continue
            sub_cost = 0 if (not is_unc and rtok == pred_toks[j - 1]) else 1
            if not is_unc and dp[i][j] == dp[i - 1][j - 1] + sub_cost:
                kept_ref_rev.append(rtok)
                kept_pred_rev.append(pred_toks[j - 1])
                if sub_cost:
                    subs += 1
                i -= 1
                j -= 1
                continue
        if i > 0:
            rtok, is_unc = ref_seq[i - 1]
            del_cost = 0 if is_unc else 1
            if dp[i][j] == dp[i - 1][j] + del_cost:
                if not is_unc:
                    kept_ref_rev.append(rtok)
                    dels += 1
                i -= 1
                continue
        kept_pred_rev.append(pred_toks[j - 1])
        ins += 1
        j -= 1

    kept_ref_rev.reverse()
    kept_pred_rev.reverse()
    return int(dp[m][n]), int(certain_count), kept_ref_rev, kept_pred_rev, subs, dels, ins


def _align_tokens_with_uncertain_ref(
    ref_raw: str,
    pred_raw: str,
    *,
    hinglish_norm: bool = False,
    mask_uncertain: bool = True,
) -> Tuple[int, int, List[str], List[str]]:
    """Return `(word_edit_dist, ref_count, kept_ref_tokens, kept_pred_tokens)`."""
    edits, cnt, kept_r, kept_p, _, _, _ = align_tokens_and_ops(
        ref_raw, pred_raw, hinglish_norm=hinglish_norm, mask_uncertain=mask_uncertain
    )
    return edits, cnt, kept_r, kept_p


def edit_distance_with_uncertain_ref(
    ref_raw: str,
    pred_raw: str,
    *,
    hinglish_norm: bool = False,
    mask_uncertain: bool = True,
) -> Tuple[int, int]:
    """Compute `(edit_errors, ref_token_count)`."""
    err, cnt, _, _ = _align_tokens_with_uncertain_ref(
        ref_raw, pred_raw, hinglish_norm=hinglish_norm, mask_uncertain=mask_uncertain
    )
    return err, cnt


def compute_char_error_counts(
    ref_raw: str,
    pred_raw: str,
    *,
    hinglish_norm: bool = False,
    mask_uncertain: bool = True,
) -> Tuple[int, int]:
    """Compute `(char_edits, ref_char_len)` over aligned tokens."""
    _, _, kept_ref, kept_pred = _align_tokens_with_uncertain_ref(
        ref_raw, pred_raw, hinglish_norm=hinglish_norm, mask_uncertain=mask_uncertain
    )
    ref_str = " ".join(kept_ref)
    pred_str = " ".join(kept_pred)
    return levenshtein_distance(list(ref_str), list(pred_str)), len(ref_str)


def extract_technical_entities(
    text: str,
    extra_acronyms: Iterable[str] = (),
    *,
    mask_uncertain: bool = True,
) -> Counter:
    clean_text = (
        UNCERTAIN_SPAN_RE.sub(" ", str(text or ""))
        if mask_uncertain
        else UNCERTAIN_SPAN_RE.sub(r"\1", str(text or ""))
    )
    counts: Counter = Counter()
    candidates = list(DEFAULT_TECHNICAL_ENTITIES) + [a for a in extra_acronyms if a]
    seen_keys: set[str] = set()
    for ent in candidates:
        k = ent.lower()
        if k in seen_keys:
            continue
        seen_keys.add(k)
        pat = re.compile(rf"\b{re.escape(ent)}\b", re.IGNORECASE)
        hits = len(pat.findall(clean_text))
        if hits > 0:
            counts[k] = hits
    return counts


def _infer_segment_track(seg: Dict[str, Any]) -> str:
    trk = str(seg.get("source_track") or "").strip().lower()
    if trk in ("mic", "system"):
        return trk
    spk = str(seg.get("speaker") or "").strip().lower()
    if "you (mic)" in spk or spk == "mic":
        return "mic"
    if spk:
        return "system"
    return ""


def _assign_predictions_to_refs(
    ref_segments: List[Dict[str, Any]],
    pred_segments: List[Dict[str, Any]],
) -> Tuple[Dict[int, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """
    Assign each predicted segment to the single verified reference segment with highest
    temporal overlap (> 0), isolating `mic` vs `system` tracks when present on both sides
    and breaking ties by IoU (Jaccard overlap).
    Predictions with zero same-track overlap against any verified ref are returned separately
    for silence-insertion / out-of-speech diagnostics.
    """
    assigned: Dict[int, List[Dict[str, Any]]] = {i: [] for i in range(len(ref_segments))}
    unmatched: List[Dict[str, Any]] = []

    ref_spans = [
        (float(r.get("start_time", r.get("start", 0.0))), float(r.get("end_time", r.get("end", 0.0))))
        for r in ref_segments
    ]
    ref_tracks = [_infer_segment_track(r) for r in ref_segments]
    available_ref_tracks = {t for t in ref_tracks if t}

    for p in pred_segments:
        ps = float(p.get("start_time", p.get("start", 0.0)))
        pe = float(p.get("end_time", p.get("end", ps)))
        p_track = _infer_segment_track(p)
        enforce_track = bool(p_track and p_track in available_ref_tracks)
        best_i = -1
        best_key = (-1.0, -1.0)
        for i, (rs, re_) in enumerate(ref_spans):
            if enforce_track and ref_tracks[i] != p_track:
                continue
            ov = min(pe, re_) - max(ps, rs)
            if ov > 0.0:
                iou = ov / max(1e-6, max(0.0, pe - ps) + max(0.0, re_ - rs) - ov)
                key = (round(ov, 4), round(iou, 6))
                if key > best_key:
                    best_key = key
                    best_i = i
        if best_i >= 0 and best_key[0] > 0.0:
            assigned[best_i].append(p)
        else:
            unmatched.append(p)

    for lst in assigned.values():
        lst.sort(key=lambda s: float(s.get("start_time", s.get("start", 0.0))))
    return assigned, unmatched


def _count_duplication_words(words: Sequence[str]) -> int:
    """Count excess words from consecutive n-gram (n >= 2) loops."""
    dup = 0
    n_tot = len(words)
    i = 0
    while i < n_tot:
        matched_n = 0
        for n in range(min(12, (n_tot - i) // 2), 1, -1):
            if list(words[i : i + n]) == list(words[i + n : i + 2 * n]):
                matched_n = n
                break
        if matched_n >= 2:
            dup += matched_n
            i += matched_n
        else:
            i += 1
    return dup


def compute_diarization_metrics(
    ref_segments: List[Dict[str, Any]],
    pred_segments: List[Dict[str, Any]],
) -> Dict[str, Optional[float]]:
    """
    Compute exact interval NIST-style DER, JER, and segment speaker attribution accuracy
    using Hungarian optimal 1-to-1 cluster-to-reference speaker mapping.
    """
    ref_intervals: List[Tuple[float, float, str]] = []
    for r in ref_segments:
        s = float(r.get("start_time", r.get("start", 0.0)))
        e = float(r.get("end_time", r.get("end", s)))
        spk = str(r.get("speaker", "Unknown Speaker")).strip() or "Unknown Speaker"
        if e > s:
            ref_intervals.append((s, e, spk))

    pred_intervals: List[Tuple[float, float, str]] = []
    for p in pred_segments:
        s = float(p.get("start_time", p.get("start", 0.0)))
        e = float(p.get("end_time", p.get("end", s)))
        spk = str(p.get("speaker", "Unknown Speaker")).strip() or "Unknown Speaker"
        if e > s:
            pred_intervals.append((s, e, spk))

    if not ref_intervals:
        return {"speaker_der": None, "speaker_jer": None, "speaker_attribution_accuracy": None}

    bounds_set = {0.0}
    for s, e, _ in ref_intervals:
        bounds_set.add(s)
        bounds_set.add(e)
    for s, e, _ in pred_intervals:
        bounds_set.add(s)
        bounds_set.add(e)
    bounds = sorted(bounds_set)

    ref_spks = sorted({spk for _, _, spk in ref_intervals})
    pred_spks = sorted({spk for _, _, spk in pred_intervals})
    ref_idx = {s: i for i, s in enumerate(ref_spks)}
    pred_idx = {s: i for i, s in enumerate(pred_spks)}

    overlap_mat = np.zeros((len(ref_spks), max(1, len(pred_spks))), dtype=np.float64)
    ref_dur = Counter()
    pred_dur = Counter()
    slices: List[Tuple[float, set[str], set[str]]] = []

    for k in range(len(bounds) - 1):
        a, b = bounds[k], bounds[k + 1]
        dt = b - a
        if dt <= 1e-6:
            continue
        mid = 0.5 * (a + b)
        r_active = {spk for s, e, spk in ref_intervals if s <= mid < e}
        p_active = {spk for s, e, spk in pred_intervals if s <= mid < e}
        if not r_active and not p_active:
            continue
        slices.append((dt, r_active, p_active))
        for r in r_active:
            ref_dur[r] += dt
        for p in p_active:
            pred_dur[p] += dt
        for r in r_active:
            for p in p_active:
                overlap_mat[ref_idx[r], pred_idx[p]] += dt

    mapping: Dict[str, str] = {}
    if pred_spks:
        row_ind, col_ind = linear_sum_assignment(-overlap_mat)
        for r_i, c_i in zip(row_ind, col_ind):
            if overlap_mat[r_i, c_i] > 0.0:
                mapping[pred_spks[c_i]] = ref_spks[r_i]

    total_ref_spk_sec = 0.0
    missed_sec = 0.0
    false_alarm_sec = 0.0
    confusion_sec = 0.0
    for dt, r_set, p_set in slices:
        n_r = len(r_set)
        n_p = len(p_set)
        total_ref_spk_sec += n_r * dt
        missed_sec += max(0, n_r - n_p) * dt
        false_alarm_sec += max(0, n_p - n_r) * dt
        mapped_p = {mapping[p] for p in p_set if p in mapping}
        correct_n = len(r_set & mapped_p)
        confusion_sec += max(0, min(n_r, n_p) - correct_n) * dt

    der = (missed_sec + false_alarm_sec + confusion_sec) / max(1e-6, total_ref_spk_sec)

    inv_map = {v: k for k, v in mapping.items()}
    jer_terms: List[float] = []
    for r in ref_spks:
        p = inv_map.get(r)
        if p is None:
            jer_terms.append(1.0)
        else:
            inter = float(overlap_mat[ref_idx[r], pred_idx[p]])
            union = float(ref_dur[r] + pred_dur[p] - inter)
            jer_terms.append(1.0 - (inter / max(1e-6, union)))
    jer = float(sum(jer_terms) / len(jer_terms)) if jer_terms else None

    # Segment-level speaker attribution accuracy
    correct_segs = 0
    for s, e, r_spk in ref_intervals:
        seg_pred_overlap: Counter = Counter()
        for ps, pe, p_spk in pred_intervals:
            ov = min(e, pe) - max(s, ps)
            if ov > 0.0:
                mapped = mapping.get(p_spk)
                if mapped:
                    seg_pred_overlap[mapped] += ov
        if seg_pred_overlap and seg_pred_overlap.most_common(1)[0][0] == r_spk:
            correct_segs += 1
    attr_acc = float(correct_segs / len(ref_intervals))

    return {
        "speaker_der": round(der, 4),
        "speaker_jer": round(jer, 4) if jer is not None else None,
        "speaker_attribution_accuracy": round(attr_acc, 4),
        "diarization_breakdown": {
            "missed_sec": round(missed_sec, 3),
            "false_alarm_sec": round(false_alarm_sec, 3),
            "confusion_sec": round(confusion_sec, 3),
            "total_ref_speaker_sec": round(total_ref_spk_sec, 3),
            "cluster_mapping": mapping,
        },
    }


def compute_word_timestamp_error_ms(
    ref_segments: List[Dict[str, Any]],
    pred_segments: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Compare aligned word start/end errors in ms when verified segments include `words` timestamps,
    or via track-assigned proportional word/boundary alignment when segments only store `[start_time, end_time]`.
    """
    pred_words: List[Tuple[str, str, float, float]] = []
    for p in pred_segments:
        for w in (p.get("words") or []):
            wt = normalize_strict_text(w.get("word", ""))
            if wt and "start" in w and "end" in w:
                pred_words.append((wt, normalize_hinglish_token(wt), float(w["start"]), float(w["end"])))

    ref_has_explicit_words = any(bool(r.get("words")) for r in ref_segments)
    if pred_words and ref_has_explicit_words:
        errs_ms: List[float] = []
        used_pred: set[int] = set()
        for r in ref_segments:
            for rw in (r.get("words") or []):
                r_tok = normalize_strict_text(rw.get("word", ""))
                if not r_tok or "start" not in rw:
                    continue
                r_norm = normalize_hinglish_token(r_tok)
                r_s = float(rw["start"])
                r_e = float(rw.get("end", r_s))
                best_idx = -1
                best_diff = 9e9
                for j, (pt, pnorm, ps, pe) in enumerate(pred_words):
                    if j in used_pred:
                        continue
                    if pt != r_tok and (not r_norm or pnorm != r_norm):
                        continue
                    diff = abs(ps - r_s) + abs(pe - r_e)
                    if diff < 4.0 and diff < best_diff:
                        best_diff = diff
                        best_idx = j
                if best_idx >= 0:
                    used_pred.add(best_idx)
                    _, _, ps, pe = pred_words[best_idx]
                    errs_ms.append(0.5 * (abs(ps - r_s) + abs(pe - r_e)) * 1000.0)

        if errs_ms:
            arr = np.array(errs_ms, dtype=np.float64)
            return {
                "median_ms": round(float(np.median(arr)), 1),
                "p95_ms": round(float(np.percentile(arr, 95)), 1),
                "matched_words": int(arr.size),
            }

    # Segment-level & proportional word-span alignment fallback for segment-verified ground truth
    assigned, _ = _assign_predictions_to_refs(ref_segments, pred_segments)
    prop_errs_ms: List[float] = []
    seg_bound_errs_ms: List[float] = []

    for i, r in enumerate(ref_segments):
        preds_i = assigned.get(i, [])
        if not preds_i:
            continue
        rs = float(r.get("start_time", r.get("start", 0.0)))
        re_ = float(r.get("end_time", r.get("end", rs)))
        r_dur = max(0.05, re_ - rs)

        ps_min = min(float(p.get("start_time", p.get("start", 0.0))) for p in preds_i)
        pe_max = max(float(p.get("end_time", p.get("end", ps_min))) for p in preds_i)
        seg_bound_errs_ms.append(0.5 * (abs(ps_min - rs) + abs(pe_max - re_)) * 1000.0)

        r_toks = tokenize_hinglish_norm(UNCERTAIN_SPAN_RE.sub(" ", str(r.get("reference_text", ""))))
        if not r_toks:
            continue
        r_spans: List[Tuple[str, float, float]] = []
        r_step = r_dur / len(r_toks)
        for k, rt in enumerate(r_toks):
            r_spans.append((rt, rs + k * r_step, rs + (k + 1) * r_step))

        p_spans: List[Tuple[str, float, float]] = []
        for p in preds_i:
            p_wlist = p.get("words") or []
            if p_wlist:
                for pw in p_wlist:
                    pt = normalize_hinglish_token(pw.get("word", ""))
                    if pt and "start" in pw and "end" in pw:
                        p_spans.append((pt, float(pw["start"]), float(pw["end"])))
            else:
                ps0 = float(p.get("start_time", p.get("start", 0.0)))
                pe0 = float(p.get("end_time", p.get("end", ps0)))
                ptoks = tokenize_hinglish_norm(str(p.get("text", "")))
                if ptoks:
                    p_step = max(0.05, pe0 - ps0) / len(ptoks)
                    for m, pt in enumerate(ptoks):
                        p_spans.append((pt, ps0 + m * p_step, ps0 + (m + 1) * p_step))

        used_p: set[int] = set()
        for rt, r_ws, r_we in r_spans:
            best_j = -1
            best_d = 9e9
            for j, (pt, p_ws, p_we) in enumerate(p_spans):
                if j in used_p or pt != rt:
                    continue
                d = abs(p_ws - r_ws) + abs(p_we - r_we)
                if d < 6.0 and d < best_d:
                    best_d = d
                    best_j = j
            if best_j >= 0:
                used_p.add(best_j)
                _, p_ws, p_we = p_spans[best_j]
                prop_errs_ms.append(0.5 * (abs(p_ws - r_ws) + abs(p_we - r_we)) * 1000.0)

    if not prop_errs_ms and not seg_bound_errs_ms:
        return None
    target_arr = np.array(prop_errs_ms if prop_errs_ms else seg_bound_errs_ms, dtype=np.float64)
    bound_arr = np.array(seg_bound_errs_ms, dtype=np.float64) if seg_bound_errs_ms else target_arr
    return {
        "median_ms": round(float(np.median(target_arr)), 1),
        "p95_ms": round(float(np.percentile(target_arr, 95)), 1),
        "matched_words": int(len(prop_errs_ms)),
        "segment_boundary_median_ms": round(float(np.median(bound_arr)), 1),
        "segment_boundary_p95_ms": round(float(np.percentile(bound_arr, 95)), 1),
    }


def _is_whole_segment_uncertain(ref_seg: Dict[str, Any]) -> bool:
    """
    Return True when a non-empty verified segment is flagged uncertain as a whole
    without inline `[?...?]` span tags (e.g., M#235 segments #9, #88, #98, #107).
    Empty reference segments (`reference_text == ""`) are never masked because they
    serve as silence-insertion ground truth.
    """
    if ref_seg.get("uncertain") is not True:
        return False
    txt = str(ref_seg.get("reference_text", ""))
    if not txt.strip():
        return False
    return UNCERTAIN_SPAN_RE.search(txt) is None


def compute_track_stream_scores(
    verified_refs: List[Dict[str, Any]],
    all_refs: List[Dict[str, Any]],
    pred_segments: List[Dict[str, Any]],
    *,
    mask_uncertain: bool = True,
    masked_ref_indices: Optional[set[int]] = None,
    assigned_map: Optional[Dict[int, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """
    Compute track-separated stream-level WER/CER, edit-op breakdown, and technical entity
    precision/recall, eliminating arbitrary segment-boundary split sensitivity across
    dual-track recordings while respecting View A uncertain masking and empty-reference audit.
    """
    masked_set = set(masked_ref_indices or set())
    excluded_pred_ids = (
        {id(p) for idx in masked_set for p in (assigned_map or {}).get(idx, [])}
        if (mask_uncertain and masked_set and assigned_map is not None)
        else set()
    )
    active_refs = (
        [r for idx, r in enumerate(verified_refs) if idx not in masked_set]
        if mask_uncertain and masked_set
        else list(verified_refs)
    )
    masked_refs_list = (
        [r for idx, r in enumerate(verified_refs) if idx in masked_set]
        if mask_uncertain and masked_set
        else []
    )
    active_preds = (
        [p for p in pred_segments if id(p) not in excluded_pred_ids]
        if excluded_pred_ids
        else list(pred_segments)
    )

    ref_tracks = sorted({
        _infer_segment_track(r)
        for r in verified_refs
        if _infer_segment_track(r)
    })
    pred_tracks = {
        _infer_segment_track(p)
        for p in pred_segments
        if _infer_segment_track(p)
    }
    use_tracks = bool(ref_tracks and pred_tracks.intersection(ref_tracks))
    stream_keys = ref_tracks if use_tracks else ["all"]
    all_verified = len(verified_refs) == len(all_refs)

    raw_w_errs = raw_w_ref = raw_subs = raw_dels = raw_ins = raw_pred_tot = 0
    norm_w_errs = norm_w_ref = norm_subs = norm_dels = norm_ins = norm_pred_tot = 0
    norm_c_errs = norm_c_ref = 0
    ref_ent_counts: Counter = Counter()
    pred_ent_counts: Counter = Counter()
    breakdown: Dict[str, Dict[str, Any]] = {}

    for trk in stream_keys:
        if use_tracks:
            r_trk = [r for r in active_refs if _infer_segment_track(r) == trk]
            u_trk = [
                u for u in all_refs
                if u.get("verified_by_human") is not True
                and _infer_segment_track(u) == trk
            ] + [m for m in masked_refs_list if _infer_segment_track(m) == trk]
            p_candidates = [p for p in active_preds if _infer_segment_track(p) == trk]
        else:
            r_trk = list(active_refs)
            u_trk = [u for u in all_refs if u.get("verified_by_human") is not True] + list(masked_refs_list)
            p_candidates = list(active_preds)

        r_trk.sort(key=lambda x: (float(x.get("start_time", x.get("start", 0.0))), float(x.get("end_time", x.get("end", 0.0)))))
        if all_verified:
            p_trk = p_candidates
        else:
            p_trk = []
            for p in p_candidates:
                ps = float(p.get("start_time", p.get("start", 0.0)))
                pe = float(p.get("end_time", p.get("end", ps)))
                v_ov = sum(
                    max(0.0, min(pe, float(r.get("end_time", r.get("end", 0.0)))) - max(ps, float(r.get("start_time", r.get("start", 0.0)))))
                    for r in r_trk
                )
                u_ov = sum(
                    max(0.0, min(pe, float(u.get("end_time", u.get("end", 0.0)))) - max(ps, float(u.get("start_time", u.get("start", 0.0)))))
                    for u in u_trk
                )
                if v_ov > u_ov and v_ov > 0.0:
                    p_trk.append(p)
        p_trk.sort(key=lambda x: (float(x.get("start_time", x.get("start", 0.0))), float(x.get("end_time", x.get("end", 0.0)))))

        r_str = " ".join(str(r.get("reference_text", "")).strip() for r in r_trk if str(r.get("reference_text", "")).strip())
        p_str = " ".join(str(p.get("text", "")).strip() for p in p_trk if str(p.get("text", "")).strip())

        rw_e, rw_n, _, kr_raw, rs_b, rd_b, ri_b = align_tokens_and_ops(
            r_str, p_str, hinglish_norm=False, mask_uncertain=mask_uncertain
        )
        nw_e, nw_n, _, kp_norm, ns_b, nd_b, ni_b = align_tokens_and_ops(
            r_str, p_str, hinglish_norm=True, mask_uncertain=mask_uncertain
        )
        nc_e, nc_n = compute_char_error_counts(r_str, p_str, hinglish_norm=True, mask_uncertain=mask_uncertain)

        raw_w_errs += rw_e
        raw_w_ref += rw_n
        raw_subs += rs_b
        raw_dels += rd_b
        raw_ins += ri_b
        raw_pred_tot += len(tokenize_strict(p_str))

        norm_w_errs += nw_e
        norm_w_ref += nw_n
        norm_subs += ns_b
        norm_dels += nd_b
        norm_ins += ni_b
        norm_pred_tot += len(tokenize_hinglish_norm(p_str))

        norm_c_errs += nc_e
        norm_c_ref += nc_n

        ref_ent_counts += extract_technical_entities(r_str, mask_uncertain=mask_uncertain)
        pred_ent_counts += extract_technical_entities(p_str, mask_uncertain=mask_uncertain)
        breakdown[trk] = {
            "raw_wer": round(rw_e / rw_n, 4) if rw_n > 0 else None,
            "hinglish_norm_wer": round(nw_e / nw_n, 4) if nw_n > 0 else None,
            "hinglish_norm_cer": round(nc_e / nc_n, 4) if nc_n > 0 else None,
            "ref_words": nw_n,
            "pred_words": len(tokenize_strict(p_str)),
            "norm_ops": {
                "substitutions": ns_b,
                "deletions": nd_b,
                "insertions": ni_b,
                "ref_words": nw_n,
                "pred_words": len(kp_norm),
            },
        }

    ref_ent_total = sum(ref_ent_counts.values())
    pred_ent_total = sum(pred_ent_counts.values())
    matched_entities = sum(min(ref_ent_counts[k], pred_ent_counts[k]) for k in ref_ent_counts)

    return {
        "stream_raw_wer": round(raw_w_errs / raw_w_ref, 4) if raw_w_ref > 0 else None,
        "stream_hinglish_norm_wer": round(norm_w_errs / norm_w_ref, 4) if norm_w_ref > 0 else None,
        "stream_hinglish_norm_cer": round(norm_c_errs / norm_c_ref, 4) if norm_c_ref > 0 else None,
        "stream_norm_ops": {
            "substitutions": norm_subs,
            "deletions": norm_dels,
            "insertions": norm_ins,
            "ref_words": norm_w_ref,
            "pred_words": norm_pred_tot,
        },
        "stream_raw_ops": {
            "substitutions": raw_subs,
            "deletions": raw_dels,
            "insertions": raw_ins,
            "ref_words": raw_w_ref,
            "pred_words": raw_pred_tot,
        },
        "stream_technical_entity_recall": round(matched_entities / ref_ent_total, 4) if ref_ent_total > 0 else None,
        "stream_technical_entity_precision": round(matched_entities / pred_ent_total, 4) if pred_ent_total > 0 else None,
        "stream_technical_entity_details": {
            "ref_total": ref_ent_total,
            "pred_total": pred_ent_total,
            "matched": matched_entities,
        },
        "track_stream_breakdown": breakdown,
    }


def _compute_accuracy_view(
    verified_refs: List[Dict[str, Any]],
    all_refs: List[Dict[str, Any]],
    pred_segments: List[Dict[str, Any]],
    assigned: Dict[int, List[Dict[str, Any]]],
    unmatched_preds: List[Dict[str, Any]],
    *,
    mask_uncertain: bool,
) -> Dict[str, Any]:
    """Compute both stream-level and segment-level WER/CER/entities under View A or View B."""
    masked_idx_set = (
        {i for i, r in enumerate(verified_refs) if _is_whole_segment_uncertain(r)}
        if mask_uncertain
        else set()
    )

    raw_w_errs = raw_w_ref = 0
    raw_c_errs = raw_c_ref = 0
    norm_w_errs = norm_w_ref = 0
    norm_c_errs = norm_c_ref = 0
    en_w_errs = en_w_ref = 0
    cs_w_errs = cs_w_ref = 0
    cs_c_errs = cs_c_ref = 0

    uncertain_token_count = 0
    uncertain_segment_count = 0
    masked_whole_segment_indices: List[int] = []
    empty_ref_indices: List[int] = []
    empty_ref_segment_count = 0
    empty_ref_insertion_words = 0

    ref_entity_counts: Counter = Counter()
    pred_entity_counts: Counter = Counter()
    non_empty_dup_words = 0
    total_dup_words = 0
    matched_pred_words = 0

    for i, ref in enumerate(verified_refs):
        r_txt = str(ref.get("reference_text", ""))
        _, unc_n = parse_reference_text(r_txt)
        is_unc_seg = unc_n > 0 or ref.get("uncertain") is True
        if is_unc_seg:
            uncertain_segment_count += 1
        uncertain_token_count += unc_n

        if i in masked_idx_set:
            masked_whole_segment_indices.append(int(ref.get("segment_index", i)))
            continue

        preds_i = assigned[i]
        p_txt = " ".join(str(p.get("text", "")).strip() for p in preds_i if str(p.get("text", "")).strip())
        p_toks = tokenize_strict(p_txt)
        seg_dup = _count_duplication_words(p_toks)
        matched_pred_words += len(p_toks)
        total_dup_words += seg_dup

        if not r_txt.strip():
            empty_ref_segment_count += 1
            empty_ref_indices.append(int(ref.get("segment_index", i)))
            empty_ref_insertion_words += len(p_toks)
            rw_e = len(p_toks)
            rw_n = 0
            nw_e = len(tokenize_hinglish_norm(p_txt))
            nw_n = 0
            rc_e, rc_n = compute_char_error_counts("", p_txt, hinglish_norm=False, mask_uncertain=False)
            nc_e, nc_n = compute_char_error_counts("", p_txt, hinglish_norm=True, mask_uncertain=False)
        else:
            non_empty_dup_words += seg_dup
            rw_e, rw_n = edit_distance_with_uncertain_ref(
                r_txt, p_txt, hinglish_norm=False, mask_uncertain=mask_uncertain
            )
            rc_e, rc_n = compute_char_error_counts(
                r_txt, p_txt, hinglish_norm=False, mask_uncertain=mask_uncertain
            )
            nw_e, nw_n = edit_distance_with_uncertain_ref(
                r_txt, p_txt, hinglish_norm=True, mask_uncertain=mask_uncertain
            )
            nc_e, nc_n = compute_char_error_counts(
                r_txt, p_txt, hinglish_norm=True, mask_uncertain=mask_uncertain
            )

        raw_w_errs += rw_e
        raw_w_ref += rw_n
        raw_c_errs += rc_e
        raw_c_ref += rc_n
        norm_w_errs += nw_e
        norm_w_ref += nw_n
        norm_c_errs += nc_e
        norm_c_ref += nc_n

        tags = {str(t).strip().lower() for t in (ref.get("language_tags") or [])}
        if "en" in tags and not tags.intersection({"hi", "hinglish", "code_switch"}):
            en_w_errs += rw_e
            en_w_ref += rw_n
        if tags.intersection({"hi", "hinglish", "code_switch"}):
            cs_w_errs += nw_e
            cs_w_ref += nw_n
            cs_c_errs += nc_e
            cs_c_ref += nc_n

        ref_entity_counts += extract_technical_entities(r_txt, mask_uncertain=mask_uncertain)
        pred_entity_counts += extract_technical_entities(p_txt, mask_uncertain=mask_uncertain)

    all_verified = len(verified_refs) == len(all_refs)
    min_vs = min(float(r.get("start_time", r.get("start", 0.0))) for r in verified_refs)
    max_ve = max(float(r.get("end_time", r.get("end", 0.0))) for r in verified_refs)
    unmatched_silence_insertion_words = 0
    for up in unmatched_preds:
        us = float(up.get("start_time", up.get("start", 0.0)))
        ue = float(up.get("end_time", up.get("end", us)))
        if all_verified or (min_vs <= us and ue <= max_ve and not any(
            min(ue, float(ar.get("end_time", 0.0))) > max(us, float(ar.get("start_time", 0.0)))
            for ar in all_refs if ar.get("verified_by_human") is not True
        )):
            w_list = tokenize_strict(str(up.get("text", "")))
            unmatched_silence_insertion_words += len(w_list)
            unm_dup = _count_duplication_words(w_list)
            total_dup_words += unm_dup
            non_empty_dup_words += unm_dup

    total_pred_words = matched_pred_words + unmatched_silence_insertion_words
    halluc_numerator = unmatched_silence_insertion_words + empty_ref_insertion_words + non_empty_dup_words
    hallucination_rate = (
        round(min(1.0, halluc_numerator / total_pred_words), 4)
        if total_pred_words > 0
        else 0.0
    )

    ref_ent_total = sum(ref_entity_counts.values())
    pred_ent_total = sum(pred_entity_counts.values())
    matched_entities = sum(min(ref_entity_counts[k], pred_entity_counts[k]) for k in ref_entity_counts)
    entity_recall = round(matched_entities / ref_ent_total, 4) if ref_ent_total > 0 else None
    entity_precision = round(matched_entities / pred_ent_total, 4) if pred_ent_total > 0 else None

    stream_scores = compute_track_stream_scores(
        verified_refs,
        all_refs,
        pred_segments,
        mask_uncertain=mask_uncertain,
        masked_ref_indices=masked_idx_set,
        assigned_map=assigned,
    )

    seg_norm_wer = round(norm_w_errs / norm_w_ref, 4) if norm_w_ref > 0 else None
    seg_raw_wer = round(raw_w_errs / raw_w_ref, 4) if raw_w_ref > 0 else None

    return {
        "mask_uncertain": mask_uncertain,
        "scored_segment_count": len(verified_refs) - len(masked_idx_set),
        "masked_whole_segment_indices": masked_whole_segment_indices,
        "uncertain_segment_count": uncertain_segment_count,
        "uncertain_token_count": uncertain_token_count,
        "empty_ref_segment_count": empty_ref_segment_count,
        "empty_ref_indices": empty_ref_indices,
        "empty_ref_insertion_words": empty_ref_insertion_words,
        "unmatched_silence_insertion_words": unmatched_silence_insertion_words,
        "duplication_words": total_dup_words,
        "hallucination_and_duplication_rate": hallucination_rate,
        "hallucination_breakdown": {
            "silence_insertion_words": unmatched_silence_insertion_words,
            "empty_ref_insertion_words": empty_ref_insertion_words,
            "duplication_words": total_dup_words,
            "total_pred_words": total_pred_words,
        },
        "segment_hinglish_norm_wer": seg_norm_wer,
        "segment_raw_wer": seg_raw_wer,
        "english_wer": round(en_w_errs / en_w_ref, 4) if en_w_ref > 0 else None,
        "hinglish_norm_wer": seg_norm_wer,
        "hinglish_norm_cer": round(norm_c_errs / norm_c_ref, 4) if norm_c_ref > 0 else None,
        "raw_wer": seg_raw_wer,
        "raw_cer": round(raw_c_errs / raw_c_ref, 4) if raw_c_ref > 0 else None,
        "hindi_code_switch_wer": round(cs_w_errs / cs_w_ref, 4) if cs_w_ref > 0 else None,
        "hindi_code_switch_cer": round(cs_c_errs / cs_c_ref, 4) if cs_c_ref > 0 else None,
        "technical_entity_recall": entity_recall,
        "technical_entity_precision": entity_precision,
        "technical_entity_details": {
            "ref_total": ref_ent_total,
            "pred_total": pred_ent_total,
            "matched": matched_entities,
        },
        **stream_scores,
    }


def score_predictions_against_reference(
    ref_data: Dict[str, Any],
    pred_segments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Score predicted segments ONLY against `verified_by_human == True` reference segments.
    Returns `status: "awaiting_human_verification"` with None metrics when 0 segments are verified.
    Produces BOTH View A (`primary_masked_uncertain`) and View B (`secondary_unmasked_complete`),
    keeping empty verified segments in silence-insertion and WER auditing.
    """
    all_refs = list(ref_data.get("segments", []))
    verified_refs = [s for s in all_refs if s.get("verified_by_human") is True]
    if not verified_refs:
        return {
            "status": "awaiting_human_verification",
            "meeting_id": ref_data.get("meeting_id"),
            "title": ref_data.get("title", ""),
            "verified_segment_count": 0,
            "total_segment_count": len(all_refs),
            "english_wer": None,
            "hinglish_norm_wer": None,
            "hinglish_norm_cer": None,
            "raw_wer": None,
            "raw_cer": None,
            "hindi_code_switch_wer": None,
            "hindi_code_switch_cer": None,
            "technical_entity_recall": None,
            "technical_entity_precision": None,
            "stream_raw_wer": None,
            "stream_hinglish_norm_wer": None,
            "stream_hinglish_norm_cer": None,
            "stream_norm_ops": None,
            "stream_raw_ops": None,
            "stream_technical_entity_recall": None,
            "stream_technical_entity_precision": None,
            "stream_technical_entity_details": None,
            "track_stream_breakdown": None,
            "hallucination_and_duplication_rate": None,
            "speaker_der": None,
            "speaker_jer": None,
            "speaker_attribution_accuracy": None,
            "word_timestamp_error_ms": None,
            "primary_masked_uncertain": None,
            "secondary_unmasked_complete": None,
            "empty_reference_audit": None,
        }

    assigned, unmatched_preds = _assign_predictions_to_refs(verified_refs, pred_segments)
    view_a = _compute_accuracy_view(
        verified_refs, all_refs, pred_segments, assigned, unmatched_preds, mask_uncertain=True
    )
    view_b = _compute_accuracy_view(
        verified_refs, all_refs, pred_segments, assigned, unmatched_preds, mask_uncertain=False
    )

    all_verified = len(verified_refs) == len(all_refs)
    diar_preds = pred_segments if all_verified else [
        p for p in pred_segments
        if any(
            min(float(p.get("end_time", p.get("end", 0.0))), float(r.get("end_time", r.get("end", 0.0))))
            > max(float(p.get("start_time", p.get("start", 0.0))), float(r.get("start_time", r.get("start", 0.0))))
            for r in verified_refs
        )
    ]
    diar_metrics = compute_diarization_metrics(verified_refs, diar_preds)
    word_ts_err = compute_word_timestamp_error_ms(verified_refs, pred_segments)

    empty_audit = {
        "empty_ref_segment_count": view_a["empty_ref_segment_count"],
        "empty_ref_indices": view_a["empty_ref_indices"],
        "empty_ref_insertion_words": view_a["empty_ref_insertion_words"],
        "unmatched_silence_insertion_words": view_a["unmatched_silence_insertion_words"],
        "handling_policy": (
            "Verified segments with empty reference_text are never masked or ignored: "
            "overlapping same-track predictions are penalized as insertions in stream WER, "
            "segment WER, and hallucination_and_duplication_rate."
        ),
    }

    return {
        "status": "scored",
        "meeting_id": ref_data.get("meeting_id"),
        "title": ref_data.get("title", ""),
        "verified_segment_count": len(verified_refs),
        "total_segment_count": len(all_refs),
        "partial_verification_only": not all_verified,
        "english_wer": view_a["english_wer"],
        "hinglish_norm_wer": view_a["hinglish_norm_wer"],
        "hinglish_norm_cer": view_a["hinglish_norm_cer"],
        "raw_wer": view_a["raw_wer"],
        "raw_cer": view_a["raw_cer"],
        "hindi_code_switch_wer": view_a["hindi_code_switch_wer"],
        "hindi_code_switch_cer": view_a["hindi_code_switch_cer"],
        "technical_entity_recall": view_a["technical_entity_recall"],
        "technical_entity_precision": view_a["technical_entity_precision"],
        "technical_entity_details": view_a["technical_entity_details"],
        "stream_raw_wer": view_a["stream_raw_wer"],
        "stream_hinglish_norm_wer": view_a["stream_hinglish_norm_wer"],
        "stream_hinglish_norm_cer": view_a["stream_hinglish_norm_cer"],
        "stream_norm_ops": view_a["stream_norm_ops"],
        "stream_raw_ops": view_a["stream_raw_ops"],
        "stream_technical_entity_recall": view_a["stream_technical_entity_recall"],
        "stream_technical_entity_precision": view_a["stream_technical_entity_precision"],
        "stream_technical_entity_details": view_a["stream_technical_entity_details"],
        "track_stream_breakdown": view_a["track_stream_breakdown"],
        "hallucination_and_duplication_rate": view_a["hallucination_and_duplication_rate"],
        "hallucination_breakdown": view_a["hallucination_breakdown"],
        "uncertain_segment_count": view_a["uncertain_segment_count"],
        "uncertain_token_count": view_a["uncertain_token_count"],
        "primary_masked_uncertain": view_a,
        "secondary_unmasked_complete": view_b,
        "empty_reference_audit": empty_audit,
        **diar_metrics,
        "word_timestamp_error_ms": word_ts_err,
    }


def load_prediction_segments(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "segments" in data and isinstance(data["segments"], list):
            return data["segments"]
    raise ValueError(f"Unsupported prediction JSON shape in {path}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score ASR predictions strictly against human-verified VOX references.")
    parser.add_argument("--reference", required=True, help="Path to M234/M235 reference JSON.")
    parser.add_argument("--prediction", required=True, help="Path to challenger prediction JSON (list or {'segments': [...]})")
    parser.add_argument("--out", default="", help="Optional output JSON report path.")
    args = parser.parse_args(argv)

    ref_data = json.loads(Path(args.reference).read_text(encoding="utf-8"))
    preds = load_prediction_segments(Path(args.prediction))
    result = score_predictions_against_reference(ref_data, preds)
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
