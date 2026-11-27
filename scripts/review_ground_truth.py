"""
Audio-Synchronized Ground-Truth Review Workbench for VOX Speech Intelligence.

Strictly read-only on original meeting recordings (`mic_track.wav`, `system_track.wav`,
`meeting_mixed.wav`). Edits ONLY reference draft JSON files (`M234_hemanth_reference_draft.json`
and `M235_rumjhum_reference_draft.json`) using atomic writes, preserving timestamps and
requiring explicit audio playback verification (`audio_listened=True`) before allowing
`verified_by_human=True`.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import threading
import urllib.parse
import wave
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
BENCH_DIR = ROOT_DIR / "data" / "benchmarks"
REC_DIR = ROOT_DIR / "data" / "recordings"

VALID_LANGUAGE_TAGS = ("en", "hi", "hinglish", "code_switch")
VALID_TRACK_FILES = {
    "mic": "mic_track.wav",
    "system": "system_track.wav",
    "mixed": "meeting_mixed.wav",
}

DEFAULT_MEETING_SPECS: Dict[int, Dict[str, Any]] = {
    234: {
        "meeting_id": 234,
        "title": "Hemanth Kumar Talathoti",
        "draft_path": BENCH_DIR / "M234_hemanth_reference_draft.json",
        "audio_dir": REC_DIR / "meeting_20261119_200303",
        "default_speakers": ["You (Mic)", "Hemanth Kumar Talathoti", "Speaker 2", "Speaker 3"],
    },
    235: {
        "meeting_id": 235,
        "title": "Rumjhum.R",
        "draft_path": BENCH_DIR / "M235_rumjhum_reference_draft.json",
        "audio_dir": REC_DIR / "meeting_20261119_200840",
        "default_speakers": ["You (Mic)", "Rumjhum.R", "Speaker 2", "Speaker 3"],
    },
    240: {
        "meeting_id": 240,
        "title": "US + Indian PLM/WFX Call (5m Slice 19:00-24:00)",
        "draft_path": BENCH_DIR / "M240_us_in_5m_reference_draft.json",
        "audio_dir": REC_DIR / "meeting_20261119_223231",
        "default_speakers": ["Speaker 1", "Speaker 2", "You (Mic)"],
    },
}


def compute_overlapping_indices(segments: List[Dict[str, Any]], min_overlap_sec: float = 0.05) -> Dict[int, List[int]]:
    """Return mapping of segment_index -> sorted list of overlapping segment indices."""
    overlaps: Dict[int, List[int]] = {}
    parsed: List[Tuple[int, float, float]] = []
    for pos, seg in enumerate(segments):
        idx = int(seg.get("segment_index", pos))
        s = float(seg.get("start_time", 0.0))
        e = float(seg.get("end_time", s))
        overlaps[idx] = []
        parsed.append((idx, s, e))

    n = len(parsed)
    for i in range(n):
        idx_i, s_i, e_i = parsed[i]
        for j in range(i + 1, n):
            idx_j, s_j, e_j = parsed[j]
            ov = min(e_i, e_j) - max(s_i, s_j)
            if ov > min_overlap_sec:
                overlaps[idx_i].append(idx_j)
                overlaps[idx_j].append(idx_i)
    return overlaps


def extract_wav_slice_bytes(
    wav_path: Path,
    start_sec: float,
    end_sec: float,
    pad_sec: float = 0.35,
) -> bytes:
    """
    Read `[start_sec - pad_sec, end_sec + pad_sec]` strictly read-only from `wav_path`
    and return an in-memory PCM WAV byte payload without mutating the source file.
    """
    if not wav_path.exists():
        raise FileNotFoundError(f"Audio track not found: {wav_path}")

    with wave.open(str(wav_path), "rb") as wf:
        nframes = wf.getnframes()
        fr = wf.getframerate()
        nch = wf.getnchannels()
        sw = wf.getsampwidth()

        s_eff = max(0.0, float(start_sec) - max(0.0, float(pad_sec)))
        e_eff = max(s_eff + 0.1, float(end_sec) + max(0.0, float(pad_sec)))
        f_start = max(0, min(nframes, int(round(s_eff * fr))))
        f_end = max(f_start, min(nframes, int(round(e_eff * fr))))
        wf.setpos(f_start)
        raw_frames = wf.readframes(f_end - f_start)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as out_wf:
        out_wf.setnchannels(nch)
        out_wf.setsampwidth(sw)
        out_wf.setframerate(fr)
        out_wf.writeframes(raw_frames)
    return buf.getvalue()


class GroundTruthStore:
    """Thread-safe atomic JSON manager for reference draft files."""

    def __init__(self, meeting_specs: Optional[Dict[int, Dict[str, Any]]] = None) -> None:
        self.meeting_specs = meeting_specs or DEFAULT_MEETING_SPECS
        self._lock = threading.RLock()

    def _spec(self, meeting_id: int) -> Dict[str, Any]:
        if meeting_id not in self.meeting_specs:
            raise KeyError(f"Unsupported meeting_id={meeting_id}")
        return self.meeting_specs[meeting_id]

    def load_meeting(self, meeting_id: int) -> Dict[str, Any]:
        spec = self._spec(meeting_id)
        draft_path = Path(spec["draft_path"])
        with self._lock:
            data = json.loads(draft_path.read_text(encoding="utf-8"))
        segs = data.get("segments", [])
        ov_map = compute_overlapping_indices(segs)
        for pos, seg in enumerate(segs):
            idx = int(seg.get("segment_index", pos))
            seg.setdefault("language_tags", [])
            seg.setdefault("uncertain", False)
            seg.setdefault("audio_listened", False)
            seg.setdefault("verified_at_utc", "")
            seg["overlaps_with"] = ov_map.get(idx, [])
            seg["has_overlap"] = bool(seg["overlaps_with"])
        data["default_speakers"] = list(spec.get("default_speakers", ["You (Mic)", "Speaker 1"]))
        return data

    def list_meetings_summary(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for mid in sorted(self.meeting_specs.keys()):
            spec = self.meeting_specs[mid]
            draft_path = Path(spec["draft_path"])
            if not draft_path.exists():
                continue
            data = self.load_meeting(mid)
            segs = data.get("segments", [])
            verified_n = sum(1 for s in segs if s.get("verified_by_human") is True)
            uncertain_n = sum(1 for s in segs if s.get("uncertain") is True or "[?" in str(s.get("reference_text", "")))
            overlap_n = sum(1 for s in segs if s.get("has_overlap"))
            out.append(
                {
                    "meeting_id": mid,
                    "title": data.get("title", spec.get("title", "")),
                    "duration_sec": data.get("duration_sec", 0.0),
                    "total_segments": len(segs),
                    "verified_segments": verified_n,
                    "uncertain_segments": uncertain_n,
                    "overlap_segments": overlap_n,
                }
            )
        return out

    def get_audio_slice(
        self,
        meeting_id: int,
        track: str,
        start_sec: float,
        end_sec: float,
        pad_sec: float = 0.35,
    ) -> bytes:
        spec = self._spec(meeting_id)
        if track not in VALID_TRACK_FILES:
            raise ValueError(f"Invalid track '{track}'. Must be one of {tuple(VALID_TRACK_FILES.keys())}")
        wav_path = Path(spec["audio_dir"]) / VALID_TRACK_FILES[track]
        return extract_wav_slice_bytes(wav_path, start_sec=start_sec, end_sec=end_sec, pad_sec=pad_sec)

    def update_segment(
        self,
        meeting_id: int,
        segment_index: int,
        payload: Dict[str, Any],
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Update editable review fields for a segment atomically.
        Never mutates `start_time`, `end_time`, `source_track`, `segment_index`, or overlap flags.
        Rejects `verified_by_human=True` unless `audio_listened` is True.
        Returns `(http_status_code, response_dict)`.
        """
        spec = self._spec(meeting_id)
        draft_path = Path(spec["draft_path"])
        with self._lock:
            data = json.loads(draft_path.read_text(encoding="utf-8"))
            segs = data.get("segments", [])
            target: Optional[Dict[str, Any]] = None
            for pos, seg in enumerate(segs):
                if int(seg.get("segment_index", pos)) == int(segment_index):
                    target = seg
                    break
            if target is None:
                return HTTPStatus.NOT_FOUND, {"error": f"Segment {segment_index} not found in meeting {meeting_id}"}

            if "audio_listened" in payload:
                eff_listened = bool(payload["audio_listened"])
            else:
                eff_listened = bool(target.get("audio_listened", False))

            wants_verified = bool(
                payload["verified_by_human"]
                if "verified_by_human" in payload
                else target.get("verified_by_human", False)
            )
            if wants_verified and not eff_listened:
                return HTTPStatus.BAD_REQUEST, {
                    "error": "Playback verification required: `audio_listened` must be true before setting `verified_by_human=true`."
                }

            if "reference_text" in payload:
                target["reference_text"] = str(payload["reference_text"])
            if "speaker" in payload:
                spk = str(payload["speaker"]).strip()
                if spk:
                    target["speaker"] = spk
            if "language_tags" in payload:
                tags = payload["language_tags"]
                if not isinstance(tags, list):
                    return HTTPStatus.BAD_REQUEST, {"error": "`language_tags` must be a list."}
                clean_tags = [str(t).strip() for t in tags if str(t).strip() in VALID_LANGUAGE_TAGS]
                target["language_tags"] = clean_tags
            else:
                target.setdefault("language_tags", [])
            if "uncertain" in payload:
                target["uncertain"] = bool(payload["uncertain"])
            else:
                target.setdefault("uncertain", False)
            if "notes" in payload:
                target["notes"] = str(payload["notes"])

            target["audio_listened"] = eff_listened
            target["verified_by_human"] = wants_verified
            if wants_verified:
                target["verified_at_utc"] = datetime.now(timezone.utc).isoformat()
            else:
                target["verified_at_utc"] = ""

            data["last_reviewed_utc"] = datetime.now(timezone.utc).isoformat()
            tmp_path = draft_path.with_name(f"{draft_path.name}.tmp")
            tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(str(tmp_path), str(draft_path))

        ov_map = compute_overlapping_indices(segs)
        out_seg = dict(target)
        out_seg["overlaps_with"] = ov_map.get(int(segment_index), [])
        out_seg["has_overlap"] = bool(out_seg["overlaps_with"])
        return HTTPStatus.OK, {"ok": True, "segment": out_seg}


WORKBENCH_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>VOX Speech Intelligence — Ground-Truth Review Workbench</title>
<style>
  :root {
    --bg: #0f131a;
    --panel: #171c26;
    --panel-2: #1e2533;
    --border: #2b3447;
    --text: #e8eef7;
    --muted: #93a1b8;
    --accent: #4f8cff;
    --green: #22c55e;
    --amber: #f59e0b;
    --rose: #f43f5e;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 18px;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
  }
  .brand { font-weight: 700; font-size: 15px; display: flex; align-items: center; gap: 10px; }
  .pill-btn {
    border: 1px solid var(--border);
    background: var(--panel-2);
    color: var(--text);
    padding: 6px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
  }
  .pill-btn.active { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
  .pill-btn.primary { background: #16a34a; border-color: #16a34a; color: #fff; font-weight: 600; }
  .pill-btn:hover { filter: brightness(1.1); }
  .progress-wrap { display: flex; align-items: center; gap: 10px; min-width: 260px; font-size: 13px; }
  .bar { flex: 1; height: 8px; background: var(--panel-2); border-radius: 99px; overflow: hidden; border: 1px solid var(--border); }
  .bar-fill { height: 100%; background: var(--green); width: 0%; transition: width 0.2s; }
  .main { display: grid; grid-template-columns: 390px 1fr; flex: 1; overflow: hidden; }
  .sidebar { border-right: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; background: var(--panel); }
  .filters { padding: 8px 10px; display: flex; gap: 6px; flex-wrap: wrap; border-bottom: 1px solid var(--border); }
  .seg-list { flex: 1; overflow-y: auto; padding: 6px; }
  .seg-item {
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 8px 10px;
    margin-bottom: 5px;
    cursor: pointer;
    background: var(--panel-2);
    font-size: 12px;
  }
  .seg-item.selected { border-color: var(--accent); background: #222e42; }
  .seg-item.verified { border-left: 3px solid var(--green); }
  .seg-top { display: flex; justify-content: space-between; align-items: center; gap: 6px; margin-bottom: 4px; color: var(--muted); }
  .badge { padding: 1px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; }
  .b-ok { background: rgba(34,197,94,0.18); color: #86efac; }
  .b-ov { background: rgba(245,158,11,0.18); color: #fcd34d; }
  .b-unc { background: rgba(244,63,94,0.18); color: #fda4af; }
  .seg-preview { color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .editor { overflow-y: auto; padding: 18px 22px; display: flex; flex-direction: column; gap: 14px; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
  .row { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
  .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); margin-bottom: 5px; display: block; }
  input[type="text"], textarea {
    width: 100%;
    background: var(--panel-2);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 6px;
    padding: 8px 10px;
    font-family: inherit;
    font-size: 14px;
  }
  textarea { min-height: 92px; resize: vertical; line-height: 1.45; }
  .readonly-box {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 12.5px;
    color: #cbd5e1;
    white-space: pre-wrap;
    line-height: 1.4;
  }
  .status-msg { font-size: 12.5px; min-height: 18px; }
  .status-err { color: var(--rose); }
  .status-ok { color: var(--green); }
  kbd { background: var(--panel-2); border: 1px solid var(--border); border-radius: 4px; padding: 1px 5px; font-size: 11px; }
</style>
</head>
<body>
<header>
  <div class="brand">
    <span>VOX Ground-Truth Review</span>
    <div id="meeting-tabs" class="row"></div>
  </div>
  <div class="progress-wrap">
    <div class="bar"><div id="prog-fill" class="bar-fill"></div></div>
    <span id="prog-label">0 / 0 verified</span>
  </div>
</header>
<div class="main">
  <aside class="sidebar">
    <div class="filters" id="filter-bar">
      <button class="pill-btn active" data-filter="all">All</button>
      <button class="pill-btn" data-filter="unverified">Unverified</button>
      <button class="pill-btn" data-filter="verified">Verified</button>
      <button class="pill-btn" data-filter="uncertain">Uncertain</button>
      <button class="pill-btn" data-filter="overlap">Overlap</button>
    </div>
    <div class="seg-list" id="seg-list"></div>
  </aside>
  <section class="editor" id="editor-pane">
    <div class="card">
      <div class="row" style="justify-content: space-between;">
        <div>
          <strong id="seg-title">Select a segment</strong>
          <div id="seg-meta" style="font-size:12px; color:var(--muted); margin-top:3px;"></div>
        </div>
        <div class="row">
          <button class="pill-btn" id="btn-prev">Prev</button>
          <button class="pill-btn" id="btn-next">Next</button>
        </div>
      </div>
    </div>

    <div class="card">
      <span class="label">Synchronized Audio Playback (<kbd>Space</kbd> Play/Pause, <kbd>1</kbd> Mic, <kbd>2</kbd> System, <kbd>3</kbd> Mixed)</span>
      <div class="row" style="margin-bottom:8px;">
        <button class="pill-btn" id="trk-mic" data-track="mic">Mic [1]</button>
        <button class="pill-btn" id="trk-system" data-track="system">System [2]</button>
        <button class="pill-btn" id="trk-mixed" data-track="mixed">Mixed [3]</button>
        <button class="pill-btn" id="btn-replay">Replay Segment</button>
        <span id="listen-badge" class="badge b-unc">Audio not yet played</span>
      </div>
      <audio id="audio-player" controls style="width:100%;"></audio>
    </div>

    <div class="card">
      <div class="row" style="align-items:flex-end;">
        <div style="flex:1; min-width:220px;">
          <span class="label">Speaker Identity (Confirmed Human Label)</span>
          <input type="text" id="inp-speaker" />
        </div>
        <div id="speaker-presets" class="row"></div>
      </div>
      <div style="margin-top:12px;">
        <span class="label">Verified Reference Text (wrap unintelligible words in <code>[?unclear?]</code>)</span>
        <textarea id="inp-ref"></textarea>
      </div>
      <div class="row" style="margin-top:10px; justify-content:space-between;">
        <div class="row">
          <span class="label" style="margin:0;">Tags:</span>
          <label><input type="checkbox" class="lang-tag" value="en" /> en</label>
          <label><input type="checkbox" class="lang-tag" value="hi" /> hi</label>
          <label><input type="checkbox" class="lang-tag" value="hinglish" /> hinglish</label>
          <label><input type="checkbox" class="lang-tag" value="code_switch" /> code_switch</label>
        </div>
        <div class="row">
          <label style="color:#fda4af;"><input type="checkbox" id="chk-uncertain" /> Uncertain speech</label>
          <label style="color:#86efac; font-weight:600;"><input type="checkbox" id="chk-verified" /> verified_by_human</label>
        </div>
      </div>
      <div style="margin-top:10px;">
        <span class="label">Notes (optional)</span>
        <input type="text" id="inp-notes" placeholder="Overlap note, technical term, accent context..." />
      </div>
      <div class="row" style="margin-top:12px; justify-content:space-between;">
        <div id="save-status" class="status-msg"></div>
        <div class="row">
          <button class="pill-btn" id="btn-save">Save</button>
          <button class="pill-btn primary" id="btn-save-next">Save &amp; Next Unverified (<kbd>Ctrl+Enter</kbd>)</button>
        </div>
      </div>
    </div>

    <div class="card">
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
        <div>
          <span class="label">Model Raw Output (`raw_text`)</span>
          <div class="readonly-box" id="box-raw"></div>
        </div>
        <div>
          <span class="label">Model Normalized Output (`display_text`)</span>
          <div class="readonly-box" id="box-display"></div>
        </div>
      </div>
      <div style="margin-top:10px;">
        <span class="label">Legacy DB Baseline Overlap (`baseline_db_text`)</span>
        <div class="readonly-box" id="box-baseline"></div>
      </div>
    </div>
  </section>
</div>
<script>
const state = {
  meetings: [],
  currentMeetingId: null,
  meetingData: null,
  selectedIdx: 0,
  filter: 'all',
  track: 'mic',
  audioPlayedForSelection: false,
};

const elList = document.getElementById('seg-list');
const audioEl = document.getElementById('audio-player');

async function fetchJson(url, opts) {
  const res = await fetch(url, opts);
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || ('HTTP ' + res.status));
  return body;
}

async function init() {
  const data = await fetchJson('/api/meetings');
  state.meetings = data.meetings || [];
  renderMeetingTabs();
  if (state.meetings.length > 0) {
    await loadMeeting(state.meetings[0].meeting_id);
  }
}

function renderMeetingTabs() {
  const wrap = document.getElementById('meeting-tabs');
  wrap.innerHTML = '';
  for (const m of state.meetings) {
    const b = document.createElement('button');
    b.className = 'pill-btn' + (m.meeting_id === state.currentMeetingId ? ' active' : '');
    b.textContent = `M#${m.meeting_id} ${m.title} (${m.verified_segments}/${m.total_segments})`;
    b.onclick = () => loadMeeting(m.meeting_id);
    wrap.appendChild(b);
  }
}

async function loadMeeting(mid) {
  state.currentMeetingId = mid;
  state.meetingData = await fetchJson('/api/meeting/' + mid);
  renderMeetingTabs();
  updateProgress();
  const segs = state.meetingData.segments || [];
  const firstUnv = segs.find(s => !s.verified_by_human);
  state.selectedIdx = firstUnv ? firstUnv.segment_index : (segs[0] ? segs[0].segment_index : 0);
  renderList();
  renderEditor();
}

function updateProgress() {
  const segs = (state.meetingData && state.meetingData.segments) || [];
  const ver = segs.filter(s => s.verified_by_human).length;
  const pct = segs.length ? Math.round((ver / segs.length) * 100) : 0;
  document.getElementById('prog-fill').style.width = pct + '%';
  document.getElementById('prog-label').textContent = `${ver} / ${segs.length} verified (${pct}%)`;
}

function filteredSegments() {
  const segs = (state.meetingData && state.meetingData.segments) || [];
  return segs.filter(s => {
    if (state.filter === 'unverified') return !s.verified_by_human;
    if (state.filter === 'verified') return !!s.verified_by_human;
    if (state.filter === 'uncertain') return !!s.uncertain || (s.reference_text || '').includes('[?');
    if (state.filter === 'overlap') return !!s.has_overlap;
    return true;
  });
}

function currentSegment() {
  const segs = (state.meetingData && state.meetingData.segments) || [];
  return segs.find(s => s.segment_index === state.selectedIdx) || segs[0] || null;
}

function renderList() {
  elList.innerHTML = '';
  for (const s of filteredSegments()) {
    const div = document.createElement('div');
    div.className = 'seg-item' + (s.segment_index === state.selectedIdx ? ' selected' : '') + (s.verified_by_human ? ' verified' : '');
    const badges = [];
    if (s.verified_by_human) badges.push('<span class="badge b-ok">VERIFIED</span>');
    if (s.has_overlap) badges.push('<span class="badge b-ov">OVERLAP</span>');
    if (s.uncertain) badges.push('<span class="badge b-unc">UNCERTAIN</span>');
    div.innerHTML = `<div class="seg-top"><span>#${s.segment_index} &middot; ${s.start_time.toFixed(2)}s–${s.end_time.toFixed(2)}s &middot; ${s.source_track}</span><span>${badges.join(' ')}</span></div>
      <div class="seg-preview"><strong>${escapeHtml(s.speaker)}</strong>: ${escapeHtml(s.reference_text || '')}</div>`;
    div.onclick = () => { state.selectedIdx = s.segment_index; renderList(); renderEditor(); };
    elList.appendChild(div);
  }
}

function renderEditor() {
  const seg = currentSegment();
  if (!seg) return;
  document.getElementById('seg-title').textContent = `Segment #${seg.segment_index} (${seg.start_time.toFixed(2)}s – ${seg.end_time.toFixed(2)}s)`;
  const ovTxt = (seg.overlaps_with && seg.overlaps_with.length) ? `Overlaps with #${seg.overlaps_with.join(', #')}` : 'No overlap';
  document.getElementById('seg-meta').textContent = `track=${seg.source_track} | cluster=${seg.speaker_cluster || ''} | duration=${(seg.end_time - seg.start_time).toFixed(2)}s | ${ovTxt}`;
  document.getElementById('inp-speaker').value = seg.speaker || '';
  document.getElementById('inp-ref').value = seg.reference_text || '';
  document.getElementById('inp-notes').value = seg.notes || '';
  document.getElementById('chk-uncertain').checked = !!seg.uncertain;
  document.getElementById('chk-verified').checked = !!seg.verified_by_human;
  document.getElementById('box-raw').textContent = seg.raw_text || '';
  document.getElementById('box-display').textContent = seg.display_text || '';
  document.getElementById('box-baseline').textContent = seg.baseline_db_text || '(none)';

  const tags = new Set(seg.language_tags || []);
  document.querySelectorAll('.lang-tag').forEach(cb => { cb.checked = tags.has(cb.value); });

  const presets = document.getElementById('speaker-presets');
  presets.innerHTML = '';
  for (const name of (state.meetingData.default_speakers || [])) {
    const b = document.createElement('button');
    b.className = 'pill-btn';
    b.textContent = name;
    b.onclick = () => { document.getElementById('inp-speaker').value = name; };
    presets.appendChild(b);
  }

  state.audioPlayedForSelection = !!seg.audio_listened;
  updateListenBadge();
  setTrack(seg.source_track === 'system' ? 'system' : (seg.source_track === 'mixed' ? 'mixed' : 'mic'), false);
  setStatus('', false);
}

function updateListenBadge() {
  const b = document.getElementById('listen-badge');
  if (state.audioPlayedForSelection) {
    b.className = 'badge b-ok';
    b.textContent = 'Audio listened (ready to verify)';
  } else {
    b.className = 'badge b-unc';
    b.textContent = 'Listen to audio before verifying';
  }
}

function setTrack(trk, autoplay) {
  state.track = trk;
  document.querySelectorAll('[data-track]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.track === trk);
  });
  const seg = currentSegment();
  if (!seg) return;
  audioEl.src = `/api/audio?meeting_id=${state.currentMeetingId}&track=${encodeURIComponent(trk)}&start=${seg.start_time}&end=${seg.end_time}&pad=0.35&t=${Date.now()}`;
  if (autoplay) audioEl.play().catch(() => {});
}

audioEl.addEventListener('play', () => {
  state.audioPlayedForSelection = true;
  updateListenBadge();
});

function setStatus(msg, isErr) {
  const el = document.getElementById('save-status');
  el.textContent = msg;
  el.className = 'status-msg ' + (isErr ? 'status-err' : 'status-ok');
}

async function saveCurrent(advanceNextUnverified) {
  const seg = currentSegment();
  if (!seg) return;
  const tags = [];
  document.querySelectorAll('.lang-tag').forEach(cb => { if (cb.checked) tags.push(cb.value); });
  const payload = {
    speaker: document.getElementById('inp-speaker').value,
    reference_text: document.getElementById('inp-ref').value,
    language_tags: tags,
    uncertain: document.getElementById('chk-uncertain').checked,
    notes: document.getElementById('inp-notes').value,
    audio_listened: state.audioPlayedForSelection,
    verified_by_human: document.getElementById('chk-verified').checked,
  };
  try {
    const res = await fetchJson(`/api/meeting/${state.currentMeetingId}/segment/${seg.segment_index}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const idxInArr = state.meetingData.segments.findIndex(s => s.segment_index === seg.segment_index);
    if (idxInArr >= 0) state.meetingData.segments[idxInArr] = res.segment;
    updateProgress();
    const mInfo = state.meetings.find(m => m.meeting_id === state.currentMeetingId);
    if (mInfo) mInfo.verified_segments = state.meetingData.segments.filter(s => s.verified_by_human).length;
    renderMeetingTabs();
    setStatus(`Saved #${seg.segment_index}`, false);
    if (advanceNextUnverified) {
      const next = state.meetingData.segments.find(s => !s.verified_by_human && s.segment_index > seg.segment_index)
                || state.meetingData.segments.find(s => !s.verified_by_human);
      if (next) state.selectedIdx = next.segment_index;
    }
    renderList();
    if (advanceNextUnverified) renderEditor();
  } catch (err) {
    setStatus(err.message || 'Save failed', true);
  }
}

document.querySelectorAll('[data-filter]').forEach(btn => {
  btn.onclick = () => {
    state.filter = btn.dataset.filter;
    document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('active', b === btn));
    renderList();
  };
});
document.querySelectorAll('[data-track]').forEach(btn => { btn.onclick = () => setTrack(btn.dataset.track, true); });
document.getElementById('btn-replay').onclick = () => { audioEl.currentTime = 0; audioEl.play().catch(() => {}); };
document.getElementById('btn-save').onclick = () => saveCurrent(false);
document.getElementById('btn-save-next').onclick = () => saveCurrent(true);
document.getElementById('btn-prev').onclick = () => moveSelection(-1);
document.getElementById('btn-next').onclick = () => moveSelection(1);

function moveSelection(delta) {
  const vis = filteredSegments();
  const pos = vis.findIndex(s => s.segment_index === state.selectedIdx);
  if (pos >= 0 && vis[pos + delta]) {
    state.selectedIdx = vis[pos + delta].segment_index;
    renderList();
    renderEditor();
  }
}

document.addEventListener('keydown', (e) => {
  const inField = ['TEXTAREA', 'INPUT'].includes(document.activeElement.tagName);
  if (e.ctrlKey && e.key === 'Enter') {
    e.preventDefault();
    saveCurrent(true);
    return;
  }
  if (!inField && e.code === 'Space') {
    e.preventDefault();
    if (audioEl.paused) audioEl.play().catch(() => {});
    else audioEl.pause();
  } else if (!inField && e.key === '1') {
    setTrack('mic', true);
  } else if (!inField && e.key === '2') {
    setTrack('system', true);
  } else if (!inField && e.key === '3') {
    setTrack('mixed', true);
  }
});

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

init();
</script>
</body>
</html>
"""


def make_request_handler(store: GroundTruthStore):
    class ReviewRequestHandler(BaseHTTPRequestHandler):
        server_version = "VOXGroundTruthWorkbench/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, status: int, obj: Any) -> None:
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path

            if path == "/" or path == "/index.html":
                raw = WORKBENCH_HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            if path == "/api/meetings":
                self._send_json(HTTPStatus.OK, {"meetings": store.list_meetings_summary()})
                return

            m_mtg = re.fullmatch(r"/api/meeting/(\d+)", path)
            if m_mtg:
                mid = int(m_mtg.group(1))
                try:
                    data = store.load_meeting(mid)
                    self._send_json(HTTPStatus.OK, data)
                except KeyError as exc:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return

            if path == "/api/audio":
                qs = urllib.parse.parse_qs(parsed.query)
                try:
                    mid = int(qs.get("meeting_id", [""])[0])
                    track = str(qs.get("track", ["mic"])[0]).strip().lower()
                    start = float(qs.get("start", ["0.0"])[0])
                    end = float(qs.get("end", ["5.0"])[0])
                    pad = float(qs.get("pad", ["0.35"])[0])
                    wav_bytes = store.get_audio_slice(mid, track=track, start_sec=start, end_sec=end, pad_sec=pad)
                except KeyError as exc:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                except (ValueError, FileNotFoundError) as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(wav_bytes)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(wav_bytes)
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown route: {path}"})

        def do_POST(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            m_seg = re.fullmatch(r"/api/meeting/(\d+)/segment/(\d+)", parsed.path)
            if not m_seg:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown route: {parsed.path}"})
                return
            mid = int(m_seg.group(1))
            seg_idx = int(m_seg.group(2))
            try:
                clen = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(clen)
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("JSON body must be an object.")
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid JSON payload: {exc}"})
                return

            try:
                status, resp = store.update_segment(mid, seg_idx, payload)
                self._send_json(status, resp)
            except KeyError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})

    return ReviewRequestHandler


def run_check(store: GroundTruthStore) -> int:
    summaries = store.list_meetings_summary()
    if not summaries:
        print("[ReviewCheck] ERROR: No reference draft files found.", flush=True)
        return 1
    for item in summaries:
        mid = item["meeting_id"]
        spec = store.meeting_specs[mid]
        adir = Path(spec["audio_dir"])
        tracks_ok = {t: (adir / fname).exists() for t, fname in VALID_TRACK_FILES.items()}
        print(
            f"[ReviewCheck] M#{mid} ({item['title']}): segs={item['total_segments']} "
            f"verified={item['verified_segments']} overlaps={item['overlap_segments']} tracks={tracks_ok}",
            flush=True,
        )
        if not all(tracks_ok.values()):
            print(f"[ReviewCheck] ERROR: Missing audio track(s) in {adir}", flush=True)
            return 1
    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VOX audio-synchronized ground-truth review workbench."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate reference drafts and read-only WAV tracks, then exit 0.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    store = GroundTruthStore(DEFAULT_MEETING_SPECS)
    if args.check:
        return run_check(store)

    server = ThreadingHTTPServer((args.host, int(args.port)), make_request_handler(store))
    print(f"[ReviewWorkbench] Listening on http://{args.host}:{args.port}/ (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
