"""Comprehensive UI/UX Redesign Acceptance Suite (Google Sans, M3 Tokens, Icons, Progressive Disclosure)."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFontInfo
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from voxlocal.core.config import AppConfig
from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.icons import VectorIconLabel, get_icon, get_pixmap
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
from voxlocal.ui.notetaker_page import NotetakerPage
from voxlocal.ui.theme import (
    DARK_TOKENS,
    LIGHT_TOKENS,
    TYPE_SCALE,
    ensure_google_fonts_loaded,
    get_html_font_family,
    get_mono_font_family,
    get_primary_font_family,
    get_type_font,
    get_ui_font,
)


class _StubSignal:
    def __init__(self):
        self._cbs = []

    def connect(self, cb):
        self._cbs.append(cb)

    def emit(self, *args, **kwargs):
        for cb in list(self._cbs):
            cb(*args, **kwargs)


class _StubController:
    def __init__(self, db: LocalDatabase, td: str):
        self.db = db
        self.theme = "light"
        self.config = AppConfig(
            storage_dir=str(Path(td) / "data"),
            notetaker_auto_start_banner_dismissed=True,
        )
        self.intelligence = MeetingIntelligenceEngine(db)
        self.meeting_state = "idle"
        self.is_meeting_recording = False
        self.active_meeting_id = None
        self.theme_changed = _StubSignal()
        self.meeting_saved_and_transcribed = _StubSignal()
        self.meeting_state_changed = _StubSignal()
        self.meeting_tick = _StubSignal()
        self.calendar_upcoming_changed = _StubSignal()
        self.dictation_interim_updated = _StubSignal()
        self.dictation_completed = _StubSignal()
        self.knowledge_updated = _StubSignal()
        self.open_notetaker_drawer_requested = _StubSignal()

    def stop_meeting_recording(self):
        pass

    def query_meeting_memory(self, query, meeting_id=None, meeting_ids=None, **kwargs):
        return self.intelligence.query(query, meeting_id=meeting_id, meeting_ids=meeting_ids)


def _contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    def _lin(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    def _lum(hx: str) -> float:
        qc = QColor(hx)
        return 0.2126 * _lin(qc.red()) + 0.7152 * _lin(qc.green()) + 0.0722 * _lin(qc.blue())

    l1 = _lum(fg_hex)
    l2 = _lum(bg_hex)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def test_bundled_google_sans_fonts_and_m3_type_scale(qapp):
    assert (ROOT / "assets" / "fonts" / "GoogleSans.ttf").is_file()
    assert (ROOT / "assets" / "fonts" / "GoogleSansCode.ttf").is_file()
    ensure_google_fonts_loaded()

    assert "Google Sans" in get_primary_font_family()
    assert "Google Sans Code" in get_mono_font_family()
    html_fam = get_html_font_family()
    assert "Google Sans" in html_fam
    assert "Figtree" not in html_fam and "EB Garamond" not in html_fam

    ui_font = get_ui_font(14, 500)
    assert QFontInfo(ui_font).family() or ui_font.family()

    for role in ("headline_large", "headline_small", "title_large", "title_medium", "body_large", "body_medium", "label_medium"):
        assert role in TYPE_SCALE
        f = get_type_font(role)
        assert f.pixelSize() >= 11


def test_tokens_parity_and_wcag_aa_contrast():
    assert set(LIGHT_TOKENS.keys()) == set(DARK_TOKENS.keys())
    assert LIGHT_TOKENS["bg"] == "#F8FAFC"
    assert LIGHT_TOKENS["surface"] == "#FFFFFF"
    assert LIGHT_TOKENS["text"] == "#0F172A"
    assert DARK_TOKENS["bg"] == "#0B0F19"
    assert DARK_TOKENS["surface"] == "#111827"
    assert DARK_TOKENS["text"] == "#F8FAFC"
    assert DARK_TOKENS["sidebar_bg"] == "#070A12"

    for name, tok in (("light", LIGHT_TOKENS), ("dark", DARK_TOKENS)):
        assert _contrast_ratio(tok["text_primary"], tok["surface"]) >= 7.0, name
        assert _contrast_ratio(tok["text_secondary"], tok["surface"]) >= 4.5, name
        assert _contrast_ratio(tok["accent_fathom"], tok["surface"]) >= 4.5, name
        assert _contrast_ratio(tok["primary_btn_text"], tok["primary_btn_bg"]) >= 4.5, name


def test_vector_icon_engine_all_icons_and_cache(qapp):
    names = [
        "home", "meetings", "calendar", "dictionary", "snippets", "style",
        "scratchpad", "insights", "settings", "sidebar_toggle", "search",
        "filter", "sort", "density_comfy", "density_compact", "folder",
        "folder_plus", "chevron_down", "chevron_right", "chevron_left",
        "close", "more_horiz", "split_view", "expand", "collapse", "record",
        "stop", "play", "pause", "skip_back", "skip_forward", "video",
        "sparkles", "copy", "export", "email", "edit", "refresh", "users",
        "lock", "globe", "drawer", "alert", "check", "bold", "list_bullet",
        "checklist", "star", "clock", "link", "plus", "trash", "send",
    ]
    for nm in names:
        pm_out = get_pixmap(nm, "#1967D2", 16, filled=False)
        pm_fill = get_pixmap(nm, "#1967D2", 16, filled=True)
        assert not pm_out.isNull(), nm
        assert not pm_fill.isNull(), nm
        assert pm_out.devicePixelRatio() == 2.0
        ic = get_icon(nm, "#1967D2", 16, filled=False)
        assert not ic.isNull()

    lbl = VectorIconLabel("◎")
    lbl.setPixmap(get_pixmap("meetings", "#1967D2", 15))
    assert lbl.text() == "◎"
    assert not lbl.pixmap().isNull()


def test_notetaker_progressive_disclosure_responsive_and_theme_speed(qapp):
    with tempfile.TemporaryDirectory(prefix="vox_ux_redesign_", ignore_cleanup_errors=True) as td:
        db = LocalDatabase(Path(td) / "vox.db")
        drawer = None
        page = None
        try:
            ctrl = _StubController(db, td)
            import wave
            wav_path = Path(td) / "sample.wav"
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b"\x00\x00" * 16000)

            m1 = db.create_meeting(
                title="Product Review Weekly",
                source_platform="Zoom",
                audio_path=str(wav_path),
            )
            db.update_meeting(
                m1,
                duration_sec=1840.0,
                transcript="Mike: Let's ship Google Sans typography and vector icons today.",
                summary="Shipped clean Google Workspace inspired UI and unified Ask VOX surface.",
                action_items=json.dumps([{"description": "Verify responsive widths", "owner": "Mike", "status": "open"}]),
                decisions=json.dumps([{"decision": "Standardize on Google Blue accent", "status": "confirmed"}]),
                status="completed",
            )
            db.replace_meeting_segments(
                m1,
                [
                    {"segment_index": 0, "speaker": "Mike", "start_time": 12.0, "end_time": 18.0, "text": "Let's ship Google Sans typography today."},
                    {"segment_index": 1, "speaker": "You (Mic)", "start_time": 24.0, "end_time": 30.0, "text": "Sounds great, checking responsive layouts.", "source_track": "mic"},
                ],
            )
            m_rec = db.create_meeting(
                title="Interrupted Sync",
                source_platform="Microsoft Teams",
                audio_path=str(Path(td) / "fake.wav"),
            )
            db.update_meeting(m_rec, duration_sec=120.0, status="interrupted")

            page = NotetakerPage(ctrl)
            page.resize(1480, 900)
            page.show()
            page.select_meeting(m1)
            page.studio.unified_tabs.setCurrentIndex(2)
            qapp.processEvents()

            # 1. Progressive disclosure: Ask rail open hides duplicate inline Ask bars
            page._toggle_ask_rail(True)
            qapp.processEvents()
            assert page.ask_rail.isVisible() is True
            assert page.studio.tab_summary.ask_box.isHidden() is True
            assert page.overall_view.prompt_strip_card.isHidden() is True

            page._toggle_ask_rail(False)
            qapp.processEvents()
            assert page.studio.tab_summary.ask_box.isHidden() is False
            assert page.overall_view.prompt_strip_card.isHidden() is False

            # 2. Concise metadata + rich technical tooltip + -10s/+10s skip controls
            assert "19" in page.lbl_sel_meta.text() or "30:40" in page.lbl_sel_meta.text() or "Zoom" in page.lbl_sel_meta.text()
            assert "Product Review Weekly" in page.lbl_sel_title.text()
            assert page.btn_skip_back.isEnabled() is True
            assert page.btn_skip_forward.isEnabled() is True
            assert page.btn_watch_video.isVisible() is False
            assert page.btn_watch_video.isEnabled() is False

            # 3. Progressive collapse of empty optional cards, non-empty decisions/actions stay expanded
            assert page.studio.tab_summary.card_decisions.is_collapsed() is False
            assert page.studio.tab_summary.card_actions.is_collapsed() is False
            assert page.studio.tab_summary.card_followups.is_collapsed() is True
            assert page.studio.tab_summary.card_questions.is_collapsed() is True

            # 4. Recoverable meeting preserves exact button label contract
            page.select_meeting(m_rec)
            qapp.processEvents()
            assert page.btn_reprocess.text() == "↻ Recover & Re-Transcribe"

            # 5. Responsive widths & Split mode without horizontal overflow on summary scroll
            page.select_meeting(m1)
            qapp.processEvents()
            assert page.lbl_sel_title.cursorPosition() == 0
            page.set_split_mode("summary_transcript")
            for w, collapse in ((1480, False), (1150, False), (1000, True)):
                page._toggle_sidebar_collapsed(collapse)
                page.resize(w, 820)
                qapp.processEvents()
                assert page.studio.tab_summary.summary_scroll.horizontalScrollBar().maximum() == 0
            page._toggle_sidebar_collapsed(False)
            page.set_split_mode("off")

            # 6. Light <-> Dark switch under 600ms
            t0 = time.perf_counter()
            page.apply_theme("dark")
            page.apply_theme("light")
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            assert elapsed_ms < 600.0, f"Theme toggle took {elapsed_ms:.1f}ms"

            # 7. Drawer vector icons & theme parity
            drawer = VoxNotetakerDrawer(controller=ctrl)
            drawer._display_meeting(m1)
            drawer.apply_theme("dark")
            drawer.apply_theme("light")
            assert not drawer.btn_back.icon().isNull()
            assert not drawer.btn_tx_copy.icon().isNull()
            assert not drawer.btn_what_did_i_miss.icon().isNull()
            assert drawer.btn_folder_pill.text() == "Add folder"
        finally:
            from PyQt6.QtCore import QUrl

            if drawer is not None:
                drawer.close()
            if page is not None:
                if hasattr(page, "media_player"):
                    page.media_player.stop()
                    page.media_player.setSource(QUrl())
                page.close()
            qapp.processEvents()
            db.close()
