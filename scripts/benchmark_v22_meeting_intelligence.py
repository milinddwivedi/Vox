"""
VOX v2.2 Unified Meeting Intelligence — Live GPU + CPU Empirical Benchmark.

Reads Meeting #284 (Amar / Northstar Apparel PLM, ~8.2 min) and Meeting #247 (~62 min)
from data/voxlocal.db in strictly READ-ONLY mode, runs full schema-constrained Qwen3.5-4B
extraction and 5 grounded Ask VOX queries on an isolated temporary MeetingDatabase, and
writes results to release_artifacts/v22_intelligence_benchmark.json.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import csv
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.engine.llm_engine import LocalLLMEngine
from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine
from voxlocal.storage.database import LocalDatabase
from voxlocal.storage.exporter import LocalMeetingSummarizer, format_seconds_hms


DB_PATH = ROOT_DIR / "data" / "voxlocal.db"
OUT_PATH = ROOT_DIR / "release_artifacts" / "v22_intelligence_benchmark.json"


def _get_gpu_vram_mb() -> Optional[int]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
        vals = [int(x.strip()) for x in out.splitlines() if x.strip().isdigit()]
        return max(vals) if vals else None
    except Exception:
        return None


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("PageFaultCount", ctypes.wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _get_tree_rss_mb() -> Dict[str, float]:
    py_mb = 0.0
    try:
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        hproc = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(hproc, ctypes.byref(counters), counters.cb):
            py_mb = counters.PeakWorkingSetSize / (1024.0 * 1024.0)
    except Exception:
        py_mb = 0.0

    llama_mb = 0.0
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        for row in csv.reader(io.StringIO(out)):
            if len(row) >= 5 and "llama-server" in row[0].lower():
                kb_str = "".join(ch for ch in row[4] if ch.isdigit())
                if kb_str:
                    llama_mb += float(kb_str) / 1024.0
    except Exception:
        pass
    return {"python_peak_rss_mb": round(py_mb, 1), "llama_server_rss_mb": round(llama_mb, 1), "total_rss_mb": round(py_mb + llama_mb, 1)}


def _read_meeting_ro(conn: sqlite3.Connection, meeting_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    if not row:
        raise RuntimeError(f"Meeting #{meeting_id} not found in {DB_PATH}")
    m = dict(row)
    segs = [
        dict(r)
        for r in conn.execute(
            "SELECT segment_index, start_time, end_time, speaker, text, confidence FROM transcript_segments WHERE meeting_id=? ORDER BY segment_index ASC, start_time ASC",
            (meeting_id,),
        ).fetchall()
    ]
    decs = [dict(r) for r in conn.execute("SELECT * FROM decisions WHERE meeting_id=?", (meeting_id,)).fetchall()]
    acts = [dict(r) for r in conn.execute("SELECT * FROM action_items WHERE meeting_id=?", (meeting_id,)).fetchall()]
    rsks = [dict(r) for r in conn.execute("SELECT * FROM risks_and_blockers WHERE meeting_id=?", (meeting_id,)).fetchall()]
    m["segments"] = segs
    m["baseline_decisions"] = decs
    m["baseline_actions"] = acts
    m["baseline_risks"] = rsks
    m["baseline_open_questions"] = json.loads(m.get("open_questions_json") or "[]")
    return m


def _normalize_db_decisions(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    state_map = {
        "confirmed": "confirmed",
        "proposed": "proposed",
        "under_discussion": "unresolved",
        "pending_confirmation": "unresolved",
        "rejected": "rejected",
        "unresolved": "unresolved",
        "unclassified": "unclassified",
    }
    out = []
    for d in analysis.get("decisions") or []:
        raw_st = str(d.get("decision_state") or d.get("status") or "confirmed").strip().lower()
        out.append({
            "decision": str(d.get("decision") or "").strip(),
            "rationale": str(d.get("rationale") or "").strip(),
            "status": state_map.get(raw_st, "confirmed"),
            "decision_state": raw_st,
            "category": str(d.get("category") or "General").strip(),
            "timestamp_sec": float(d.get("timestamp_sec") or 0.0),
            "speaker": str(d.get("speaker") or "").strip(),
            "context": str(d.get("context") or "").strip(),
            "evidence_quote": str(d.get("evidence_quote") or "").strip(),
        })
    return out


def _seed_temp_db_with_v22(
    db: LocalDatabase,
    m284: Dict[str, Any],
    analysis_284: Dict[str, Any],
    recent_others: List[Dict[str, Any]],
) -> int:
    # Seed older/companion meetings first so #284-copy appears latest, preserving realistic ordering
    for om in recent_others:
        mid = db.create_meeting(
            title=str(om.get("title") or "Meeting"),
            source_platform=str(om.get("source_platform") or "Microsoft Teams"),
            capture_mode=str(om.get("capture_mode") or "system_and_mic"),
        )
        db.update_meeting(
            mid,
            duration_sec=float(om.get("duration_sec") or 0.0),
            status="completed",
            summary=str(om.get("summary") or ""),
            brief_summary=str(om.get("brief_summary") or om.get("summary") or ""),
            detailed_summary=str(om.get("detailed_summary") or ""),
            open_questions_json=str(om.get("open_questions_json") or "[]"),
        )
        for seg in om.get("segments") or []:
            db.add_transcript_segment(
                meeting_id=mid,
                segment_index=int(seg.get("segment_index") or 0),
                start_time=float(seg.get("start_time") or 0.0),
                end_time=float(seg.get("end_time") or 0.0),
                speaker=str(seg.get("speaker") or "Speaker"),
                text=str(seg.get("text") or ""),
                confidence=float(seg.get("confidence") or 0.9),
            )
        for a in om.get("baseline_actions") or []:
            db.add_action_item(
                mid,
                str(a.get("description") or ""),
                owner=a.get("owner") or "Milind",
                due_date=a.get("due_date"),
                priority=a.get("priority") or "",
                timestamp_sec=float(a.get("timestamp_sec") or 0.0),
            )
        for d in om.get("baseline_decisions") or []:
            db.add_decision(
                mid,
                str(d.get("decision") or ""),
                rationale=str(d.get("rationale") or ""),
                status=str(d.get("status") or "confirmed"),
                category=str(d.get("category") or "General"),
                timestamp_sec=float(d.get("timestamp_sec") or 0.0),
            )

    mid_284 = db.create_meeting(
        title=str(m284.get("title") or "Microsoft Teams Meeting"),
        source_platform=str(m284.get("source_platform") or "Microsoft Teams (Browser)"),
        capture_mode=str(m284.get("capture_mode") or "system_and_mic"),
    )
    db.update_meeting(
        mid_284,
        duration_sec=float(m284.get("duration_sec") or 0.0),
        status="completed",
        full_transcript=str(m284.get("full_transcript") or ""),
        summary=str(analysis_284.get("summary") or ""),
        brief_summary=str(analysis_284.get("brief_summary") or analysis_284.get("summary") or ""),
        detailed_summary=str(analysis_284.get("detailed_summary") or ""),
        topics_json=json.dumps(analysis_284.get("topics") or [], ensure_ascii=False),
        key_points_json=json.dumps(analysis_284.get("key_points") or [], ensure_ascii=False),
        follow_ups_json=json.dumps(analysis_284.get("follow_ups") or [], ensure_ascii=False),
        open_questions_json=json.dumps(analysis_284.get("open_questions") or [], ensure_ascii=False),
    )
    for seg in m284.get("segments") or []:
        db.add_transcript_segment(
            meeting_id=mid_284,
            segment_index=int(seg.get("segment_index") or 0),
            start_time=float(seg.get("start_time") or 0.0),
            end_time=float(seg.get("end_time") or 0.0),
            speaker=str(seg.get("speaker") or "Speaker"),
            text=str(seg.get("text") or ""),
            confidence=float(seg.get("confidence") or 0.9),
        )
    for d in _normalize_db_decisions(analysis_284):
        db.add_decision(
            mid_284,
            d["decision"],
            rationale=d["rationale"],
            status=d["status"],
            decision_state=d["decision_state"],
            category=d["category"],
            timestamp_sec=d["timestamp_sec"],
            speaker=d["speaker"],
            context=d["context"],
            evidence_quote=d["evidence_quote"],
        )
    for a in analysis_284.get("action_items") or []:
        db.add_action_item(
            mid_284,
            str(a.get("description") or ""),
            owner=a.get("owner"),
            due_date=a.get("due_date"),
            priority=str(a.get("priority") or ""),
            due_date_resolution=str(a.get("due_date_resolution") or ""),
            confirmation_state=str(a.get("confirmation_state") or "needs_confirmation"),
            related_topic=str(a.get("related_topic") or ""),
            timestamp_sec=float(a.get("timestamp_sec") or 0.0),
            evidence_quote=str(a.get("evidence_quote") or ""),
        )
    for r in analysis_284.get("risks") or []:
        raw_itype = str(r.get("item_type") or "risk").lower()
        db_itype = "blocker" if raw_itype == "blocker" else "risk"
        desc_txt = str(r.get("description") or "").strip()
        if not desc_txt:
            continue
        db.add_risk_or_blocker(
            mid_284,
            item_type=db_itype,
            title=desc_txt[:90],
            description=desc_txt,
            impact=str(r.get("impact") or "medium").lower(),
            status=None,
            mitigation_or_resolution=str(r.get("mitigation_or_resolution") or ""),
            source_timestamp_sec=float(r.get("timestamp_sec") or 0.0),
            evidence_quote=str(r.get("evidence_quote") or ""),
        )
    return mid_284


def main() -> int:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        m284 = _read_meeting_ro(conn, 284)
        m247 = _read_meeting_ro(conn, 247)
        recent_rows = conn.execute(
            "SELECT id FROM meetings WHERE id < 284 AND status='completed' AND duration_sec > 60 ORDER BY id DESC LIMIT 2"
        ).fetchall()
        recent_others = [_read_meeting_ro(conn, int(r["id"])) for r in reversed(recent_rows)]
    finally:
        conn.close()

    report: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "meeting_284_baseline": {
            "id": 284,
            "title": m284.get("title"),
            "duration_sec": m284.get("duration_sec"),
            "segment_count": len(m284["segments"]),
            "decisions_count": len(m284["baseline_decisions"]),
            "decisions": [{"decision": d.get("decision"), "status": d.get("status")} for d in m284["baseline_decisions"]],
            "action_items_count": len(m284["baseline_actions"]),
            "action_items": [
                {"description": a.get("description"), "owner": a.get("owner"), "due_date": a.get("due_date")}
                for a in m284["baseline_actions"]
            ],
            "risks_count": len(m284["baseline_risks"]),
            "risks": [r.get("description") for r in m284["baseline_risks"]],
            "open_questions_count": len(m284["baseline_open_questions"]),
            "open_questions": [q.get("question") for q in m284["baseline_open_questions"]],
        },
    }

    gpu_engine: Optional[LocalLLMEngine] = None
    cpu_engine: Optional[LocalLLMEngine] = None

    try:
        # ------------------------------------------------------------------
        # 1. GPU (Vulkan) Meeting #284 + 5 Ask VOX queries + Meeting #247 (62m)
        # ------------------------------------------------------------------
        gpu_engine = LocalLLMEngine(device="auto")
        t0 = time.perf_counter()
        analysis_284_gpu = LocalMeetingSummarizer.analyze_full(
            m284["segments"],
            "\n".join(s.get("text") or "" for s in m284["segments"]),
            duration_sec=float(m284.get("duration_sec") or 0.0),
            title=str(m284.get("title") or "Microsoft Teams Meeting"),
            platform=str(m284.get("source_platform") or "Microsoft Teams (Browser)"),
            llm_engine=gpu_engine,
            meeting_timestamp=str(m284.get("created_at") or ""),
            local_user_name="Milind",
        )
        t_284_gpu = round(time.perf_counter() - t0, 2)
        mem_284_gpu = _get_tree_rss_mb()
        vram_284_gpu = _get_gpu_vram_mb()

        report["meeting_284_gpu_v22"] = {
            "latency_sec": t_284_gpu,
            "vram_used_mb": vram_284_gpu,
            "memory": mem_284_gpu,
            "llm_model": analysis_284_gpu.get("llm_model"),
            "llm_device": analysis_284_gpu.get("llm_device"),
            "llm_reliability": analysis_284_gpu.get("llm_reliability"),
            "brief_summary": analysis_284_gpu.get("brief_summary"),
            "decisions_count": len(analysis_284_gpu.get("decisions") or []),
            "decisions": [
                {
                    "decision": d.get("decision"),
                    "status": d.get("status"),
                    "decision_state": d.get("decision_state"),
                    "speaker": d.get("speaker"),
                    "timestamp_sec": d.get("timestamp_sec"),
                }
                for d in (analysis_284_gpu.get("decisions") or [])
            ],
            "action_items_count": len(analysis_284_gpu.get("action_items") or []),
            "action_items": [
                {
                    "description": a.get("description"),
                    "owner": a.get("owner"),
                    "due_date": a.get("due_date"),
                    "priority": a.get("priority"),
                    "confirmation_state": a.get("confirmation_state"),
                    "timestamp_sec": a.get("timestamp_sec"),
                }
                for a in (analysis_284_gpu.get("action_items") or [])
            ],
            "risks_count": len(analysis_284_gpu.get("risks") or []),
            "risks": analysis_284_gpu.get("risks") or [],
            "open_questions_count": len(analysis_284_gpu.get("open_questions") or []),
            "open_questions": analysis_284_gpu.get("open_questions") or [],
        }
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        # Run 5 Ask VOX queries in an isolated temp DB
        ask_results: List[Dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as td:
            tmp_db = LocalDatabase(Path(td) / "v22_bench.db")
            mid_284_tmp = _seed_temp_db_with_v22(tmp_db, m284, analysis_284_gpu, recent_others)
            intel = MeetingIntelligenceEngine(db=tmp_db, llm_engine=gpu_engine)
            test_queries = [
                ("Q1_customer_notes", "Prepare notes of this meeting to share with the customer.", mid_284_tmp),
                ("Q2_speaker_amar", "What did Amar say about missing buyer references?", mid_284_tmp),
                ("Q3_followup_email", "Prepare a follow-up email for this meeting.", mid_284_tmp),
                ("Q4_last_3_only_me", "Summarize the last 3 meetings and list action items that belong only to me.", None),
                ("Q5_negative_trap_500k", "What was the $500k budget decision?", mid_284_tmp),
            ]
            for qname, qtext, scoped_mid in test_queries:
                qt0 = time.perf_counter()
                res = intel.query(
                    qtext,
                    meeting_id=scoped_mid,
                    answer_depth="detailed",
                    local_user_name="Milind",
                )
                qlat = round(time.perf_counter() - qt0, 2)
                cits = res.get("citations") or []
                nonzero_cits = [
                    format_seconds_hms(float(c.get("start_sec") or c.get("timestamp_sec") or c.get("start_time") or 0.0))
                    for c in cits
                    if float(c.get("start_sec") or c.get("timestamp_sec") or c.get("start_time") or 0.0) > 0
                ]
                ask_results.append({
                    "id": qname,
                    "question": qtext,
                    "scoped_meeting_id": scoped_mid,
                    "effective_scope_label": res.get("effective_scope_label"),
                    "intent": res.get("intent"),
                    "latency_sec": qlat,
                    "citation_count": len(cits),
                    "nonzero_citation_timestamps": nonzero_cits[:6],
                    "answer": res.get("answer"),
                })
            tmp_db.close()
        report["ask_vox_queries"] = ask_results
        OUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        # Meeting #247 (62 min, 366 segments) on Vulkan GPU
        t247 = time.perf_counter()
        analysis_247_gpu = LocalMeetingSummarizer.analyze_full(
            m247["segments"],
            "\n".join(s.get("text") or "" for s in m247["segments"]),
            duration_sec=float(m247.get("duration_sec") or 0.0),
            title=str(m247.get("title") or "Zoom Meeting"),
            platform=str(m247.get("source_platform") or "Zoom"),
            llm_engine=gpu_engine,
            meeting_timestamp=str(m247.get("created_at") or ""),
            local_user_name="Milind",
        )
        report["meeting_247_62min_gpu"] = {
            "id": 247,
            "duration_sec": m247.get("duration_sec"),
            "segment_count": len(m247["segments"]),
            "latency_sec": round(time.perf_counter() - t247, 2),
            "vram_used_mb": _get_gpu_vram_mb(),
            "memory": _get_tree_rss_mb(),
            "llm_model": analysis_247_gpu.get("llm_model"),
            "llm_device": analysis_247_gpu.get("llm_device"),
            "llm_reliability": analysis_247_gpu.get("llm_reliability"),
            "decisions_count": len(analysis_247_gpu.get("decisions") or []),
            "action_items_count": len(analysis_247_gpu.get("action_items") or []),
            "risks_count": len(analysis_247_gpu.get("risks") or []),
            "open_questions_count": len(analysis_247_gpu.get("open_questions") or []),
        }
        OUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        gpu_engine.shutdown()
        gpu_engine = None
        time.sleep(1.0)

        # ------------------------------------------------------------------
        # 2. CPU Fallback Benchmark on Meeting #284
        # ------------------------------------------------------------------
        cpu_engine = LocalLLMEngine(device="cpu")
        t_cpu0 = time.perf_counter()
        analysis_284_cpu = LocalMeetingSummarizer.analyze_full(
            m284["segments"],
            "\n".join(s.get("text") or "" for s in m284["segments"]),
            duration_sec=float(m284.get("duration_sec") or 0.0),
            title=str(m284.get("title") or "Microsoft Teams Meeting"),
            platform=str(m284.get("source_platform") or "Microsoft Teams (Browser)"),
            llm_engine=cpu_engine,
            meeting_timestamp=str(m284.get("created_at") or ""),
            local_user_name="Milind",
        )
        report["meeting_284_cpu_v22"] = {
            "latency_sec": round(time.perf_counter() - t_cpu0, 2),
            "memory": _get_tree_rss_mb(),
            "llm_model": analysis_284_cpu.get("llm_model"),
            "llm_device": analysis_284_cpu.get("llm_device"),
            "llm_reliability": analysis_284_cpu.get("llm_reliability"),
            "decisions_count": len(analysis_284_cpu.get("decisions") or []),
            "action_items_count": len(analysis_284_cpu.get("action_items") or []),
            "risks_count": len(analysis_284_cpu.get("risks") or []),
            "open_questions_count": len(analysis_284_cpu.get("open_questions") or []),
        }
    finally:
        if gpu_engine is not None:
            try:
                gpu_engine.shutdown()
            except Exception:
                pass
        if cpu_engine is not None:
            try:
                cpu_engine.shutdown()
            except Exception:
                pass

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    g = report["meeting_284_gpu_v22"]
    c = report["meeting_284_cpu_v22"]
    long_m = report["meeting_247_62min_gpu"]
    print(f"\nBenchmark saved -> {OUT_PATH}")
    print(
        f"Meeting #284 GPU: {g['latency_sec']}s | dec={g['decisions_count']} act={g['action_items_count']} "
        f"risk={g['risks_count']} oq={g['open_questions_count']} | VRAM={g['vram_used_mb']}MB | RSS={g['memory']['total_rss_mb']}MB"
    )
    print(
        f"Meeting #284 CPU: {c['latency_sec']}s | dec={c['decisions_count']} act={c['action_items_count']} "
        f"risk={c['risks_count']} oq={c['open_questions_count']} | RSS={c['memory']['total_rss_mb']}MB"
    )
    print(
        f"Meeting #247 (62m) GPU: {long_m['latency_sec']}s | dec={long_m['decisions_count']} act={long_m['action_items_count']} "
        f"risk={long_m['risks_count']} oq={long_m['open_questions_count']} | VRAM={long_m['vram_used_mb']}MB"
    )
    for aq in report["ask_vox_queries"]:
        print(f"Ask VOX [{aq['id']}] {aq['latency_sec']}s | intent={aq['intent']} | cits={aq['citation_count']} | nonzero={aq['nonzero_citation_timestamps']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
