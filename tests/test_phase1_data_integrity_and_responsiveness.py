"""
Phase 1 Verification Suite: Data Integrity & UI Responsiveness.

Covers:
1. Summary & Risks debounced auto-save, status indicator, reload persistence, and flush on meeting switch.
2. Virtualized interactive transcript (QListView + TranscriptListModel + TranscriptItemDelegate):
   - 1,500-segment performance + zero per-segment QFrame widgets inside list_view viewport
   - Timestamp click seek -> seek_requested + NotetakerPage._seek_to_seconds
   - Speaker rename -> SQLite rename_meeting_speaker persistence + filter refresh
   - Multi-row selection & fallback clipboard copy
   - Search & speaker filtering, highlight_time, and resize cache invalidation
3. Debounced search responsiveness on HomePage, CalendarPage, NotetakerPage, and VoxCommandPaletteDialog.
4. VoxNotetakerDrawer reliability:
   - Absence of hardcoded sample strings ("HR Connect Session", "MH", "Role and Satisfaction", "Tools & Action Items")
   - Working btn_folder_pill DB assignment, btn_more & btn_share menus
   - seek: / play: anchor clicking -> seek_requested(meeting_id, seconds)
   - Dark Mode HTML token fidelity (no light hex leaks)
   - Scroll Lock & Jump to Live behavior
   - Debounced notes auto-save + flush on switch
   - '/' slash skills visibility/filtering & worker-thread Ask VOX execution
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent, QItemSelectionModel, QPointF, QRect, Qt, QUrl
from PyQt6.QtGui import QFontMetrics, QMouseEvent
from PyQt6.QtWidgets import QApplication, QFrame, QStyleOptionViewItem

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.calendar_page import CalendarPage
from voxlocal.ui.command_palette import PaletteCommand, VoxCommandPaletteDialog
from voxlocal.ui.hub_pages import HomePage
from voxlocal.ui.meeting_studio import (
    TranscriptItemDelegate,
    TranscriptListModel,
    VoxInteractiveTranscriptTab,
    VoxMeetingStudioWorkspace,
    VoxSummaryDeliverablesTab,
)
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
from voxlocal.ui.notetaker_page import NotetakerPage
from voxlocal.win32.startup import StartupManager


def _get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([sys.argv[0], "--platform", "offscreen"])
    return app


def _wait_until(predicate, timeout_sec: float = 2.0) -> bool:
    app = _get_qapp()
    deadline = time.perf_counter() + timeout_sec
    while time.perf_counter() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.015)
    app.processEvents()
    return bool(predicate())


_RETIRED_CONTROLLERS: list = []


class _NoOpThread:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass


def _shutdown_controller(ctrl: AppController, *widgets) -> None:
    app = _get_qapp()
    for w in widgets:
        if w is not None:
            try:
                if hasattr(w, "media_player"):
                    w.media_player.stop()
                    w.media_player.setSource(QUrl())
                w.close()
                w.deleteLater()
            except Exception:
                pass
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    eng = getattr(ctrl, "calendar_engine", None)
    if eng is not None and hasattr(eng, "stop_polling"):
        try:
            eng.stop_polling()
        except Exception:
            pass
    ctrl.shutdown()
    _RETIRED_CONTROLLERS.append(ctrl)
    app.processEvents()


def _create_controller(temp_dir: tempfile.TemporaryDirectory) -> AppController:
    cfg = AppConfig(storage_dir=str(temp_dir.name))
    cfg.launch_at_login = StartupManager.is_enabled()
    cfg.disable_model_preload = True
    cfg.disable_hotkeys = True
    real_thread = threading.Thread

    def _safe_thread(*args, **kwargs):
        if kwargs.get("name") == "StartupUpdateCheck":
            return _NoOpThread()
        return real_thread(*args, **kwargs)

    with patch("threading.Thread", side_effect=_safe_thread):
        return AppController(cfg)


class TestPhase1DataIntegrityAndResponsiveness(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _get_qapp()

    # ------------------------------------------------------------------
    # 1. Summary & Risks Auto-Save + Flush on Meeting Switch
    # ------------------------------------------------------------------
    def test_01_summary_and_risks_autosave_and_switch_flush(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p1_sum_") as td:
            db = LocalDatabase(Path(td) / "studio.db")
            m1 = db.create_meeting(title="Architecture Review")
            m2 = db.create_meeting(title="Budget Sync")
            db.update_meeting(m1, summary="Initial M1 summary.")
            db.update_meeting(m2, summary="Initial M2 summary.")

            workspace = VoxMeetingStudioWorkspace(theme="dark", db=db)
            workspace.load_meeting(m1)
            tab: VoxSummaryDeliverablesTab = workspace.tab_summary

            # Speed up test debounce slightly? Keep default 600ms and wait deterministically.
            tab.txt_summary.setPlainText("Updated M1 executive summary with API metrics.")
            tab.txt_risks.setPlainText("• Delay in GPU quota\n• Pending security review")
            self.assertEqual(tab.lbl_summary_save_status.text(), "Saving...")

            saved = _wait_until(lambda: tab.lbl_summary_save_status.text() == "✓ Saved", timeout_sec=2.0)
            self.assertTrue(saved, f"Expected '✓ Saved', got {tab.lbl_summary_save_status.text()!r}")

            reloaded_m1 = db.get_meeting(m1)
            self.assertIsNotNone(reloaded_m1)
            self.assertEqual(reloaded_m1["summary"], "Updated M1 executive summary with API metrics.")
            self.assertEqual(reloaded_m1["user_edited"], 1)
            risks_m1 = reloaded_m1.get("risks_and_blockers_list", [])
            self.assertEqual(len(risks_m1), 2)
            self.assertEqual(risks_m1[0]["description"], "Delay in GPU quota")

            # Reloading m1 into workspace restores exact risks_and_blockers_list
            workspace.load_meeting(m2)
            workspace.load_meeting(m1)
            self.assertIn("Delay in GPU quota", tab.txt_risks.toPlainText())
            self.assertIn("Pending security review", tab.txt_risks.toPlainText())

            # Edit m1 and switch immediately to m2 BEFORE timer expires -> flush_pending_saves must persist m1 cleanly
            tab.txt_summary.setPlainText("Flushed M1 summary before switch.")
            tab.txt_risks.setPlainText("• Urgent blocker prior to switch")
            self.assertTrue(tab._save_timer.isActive())

            workspace.load_meeting(m2)
            self.assertFalse(tab._save_timer.isActive())
            self.assertEqual(tab.txt_summary.toPlainText(), "Initial M2 summary.")

            persisted_m1 = db.get_meeting(m1)
            persisted_m2 = db.get_meeting(m2)
            self.assertEqual(persisted_m1["summary"], "Flushed M1 summary before switch.")
            self.assertEqual(len(persisted_m1["risks_and_blockers_list"]), 1)
            self.assertEqual(persisted_m1["risks_and_blockers_list"][0]["description"], "Urgent blocker prior to switch")
            self.assertEqual(persisted_m2["summary"], "Initial M2 summary.")
            self.assertEqual(len(persisted_m2.get("risks_and_blockers_list", [])), 0)

            workspace.close()
            workspace.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()

    # ------------------------------------------------------------------
    # 2. Virtualized Transcript: 1,500 rows, Seek, Rename, Selection, Filter, Resize
    # ------------------------------------------------------------------
    def test_02_virtualized_transcript_performance_and_interactions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p1_tx_") as td:
            db = LocalDatabase(Path(td) / "tx.db")
            m_id = db.create_meeting(title="Massive All-Hands")

            # Seed DB segments for rename persistence verification
            for i in range(6):
                db.add_transcript_segment(
                    meeting_id=m_id,
                    segment_index=i,
                    start_time=float(i * 10),
                    end_time=float(i * 10 + 8),
                    speaker="Speaker 1" if i % 2 == 0 else "Bob",
                    text=f"Segment number {i} discussing latency and architecture.",
                )

            large_segments = [
                {
                    "speaker": "Speaker 1" if i % 2 == 0 else "Bob",
                    "start_time": float(i * 4),
                    "end_time": float(i * 4 + 3.5),
                    "text": f"Turn {i}: scaling benchmark target alpha-{i} for virtualized rendering.",
                    "source_track": "mic" if i % 2 == 0 else "system",
                }
                for i in range(1500)
            ]

            tab = VoxInteractiveTranscriptTab(theme="dark", db=db)
            tab.meeting_id = m_id
            tab.resize(780, 620)
            tab.show()

            t0 = time.perf_counter()
            tab.load_segments(large_segments)
            tab.search_input.setText("alpha-1499")
            self.app.processEvents()
            elapsed = time.perf_counter() - t0

            self.assertLess(elapsed, 1.5, f"1500-segment load+filter too slow: {elapsed:.3f}s")
            self.assertEqual(tab._model.rowCount(), 1)
            self.assertEqual(tab.lbl_match_count.text(), "1 matches")
            # Zero per-segment QFrame cards instantiated inside viewport
            self.assertEqual(len(tab.list_view.viewport().findChildren(QFrame)), 0)

            # Clear filter and test speaker filter + highlight_time + resize cache invalidation
            tab._reset_filter()
            self.app.processEvents()
            self.assertEqual(tab._model.rowCount(), 1500)
            self.assertEqual(tab.lbl_match_count.text(), "1500 matches")

            tab.highlight_time(400.0)
            self.app.processEvents()
            idx_100 = tab._model.index(100, 0)
            self.assertTrue(bool(idx_100.data(TranscriptListModel.ROLE_IS_ACTIVE)))

            # Populate delegate size cache via sizeHint then resize -> clear_cache fires and old width entries are gone
            opt = QStyleOptionViewItem()
            opt.rect = QRect(0, 0, 1234, 100)
            tab._delegate.sizeHint(opt, tab._model.index(0, 0))
            self.assertIn((0, 1234, "dark"), tab._delegate._size_cache)
            with patch.object(tab._delegate, "clear_cache", wraps=tab._delegate.clear_cache) as mock_clear_cache:
                tab.resize(620, 500)
                self.app.processEvents()
                self.assertGreaterEqual(mock_clear_cache.call_count, 1)
            self.assertNotIn((0, 1234, "dark"), tab._delegate._size_cache)

            # Click ts_rect & speaker_rect through delegate.editorEvent
            seeks = []
            tab.seek_requested.connect(lambda sec: seeks.append(sec))
            row5_idx = tab._model.index(5, 0)
            row_rect = QRect(0, 0, 680, 90)
            opt.rect = row_rect
            spk = str(row5_idx.data(TranscriptListModel.ROLE_SPEAKER))
            st = float(row5_idx.data(TranscriptListModel.ROLE_START_TIME))
            ts_label = f"{int(st)//60:02d}:{int(st)%60:02d}"
            spk_font, ts_font, _ = tab._delegate._fonts(opt)
            _, spk_rect, ts_rect, _ = TranscriptItemDelegate._layout_rects(
                row_rect, spk, ts_label, QFontMetrics(spk_font), QFontMetrics(ts_font)
            )
            ts_click = QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                QPointF(ts_rect.center()),
                QPointF(ts_rect.center()),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            handled = tab._delegate.editorEvent(ts_click, tab._model, opt, row5_idx)
            self.assertTrue(handled)
            self.assertEqual(seeks, [20.0])

            # Multi-row selection & clipboard copy vs unselected copy-all
            tab.load_meeting(m_id)
            sel = tab.list_view.selectionModel()
            sel.select(tab._model.index(1, 0), QItemSelectionModel.SelectionFlag.Select)
            sel.select(tab._model.index(3, 0), QItemSelectionModel.SelectionFlag.Select)
            selected_txt = tab.copy_selected_or_all()
            self.assertEqual(len(selected_txt.splitlines()), 2)
            self.assertIn("Segment number 1", selected_txt)
            self.assertIn("Segment number 3", selected_txt)
            self.assertNotIn("Segment number 0", selected_txt)
            self.assertEqual(QApplication.clipboard().text(), selected_txt)

            sel.clearSelection()
            all_txt = tab.copy_selected_or_all()
            self.assertEqual(len(all_txt.splitlines()), 6)

            # Speaker rename via speaker_rect click -> SQLite + model + combo persistence
            row0_idx = tab._model.index(0, 0)
            opt.rect = QRect(0, 0, 680, 90)
            spk0 = str(row0_idx.data(TranscriptListModel.ROLE_SPEAKER))
            st0 = float(row0_idx.data(TranscriptListModel.ROLE_START_TIME))
            _, spk0_rect, _, _ = TranscriptItemDelegate._layout_rects(
                opt.rect, spk0, f"{int(st0)//60:02d}:{int(st0)%60:02d}", QFontMetrics(spk_font), QFontMetrics(ts_font)
            )
            spk_click = QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                QPointF(spk0_rect.center()),
                QPointF(spk0_rect.center()),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            with patch.object(tab, "_get_text_dialog", return_value=("Alice Chen", True)):
                tab._delegate.editorEvent(spk_click, tab._model, opt, row0_idx)

            db_segs = db.get_meeting_segments(m_id)
            self.assertTrue(all(s["speaker"] == "Alice Chen" for idx, s in enumerate(db_segs) if idx % 2 == 0))
            filter_items = [tab.combo_speaker_filter.itemData(i) for i in range(tab.combo_speaker_filter.count())]
            self.assertIn("Alice Chen", filter_items)
            self.assertNotIn("Speaker 1", filter_items)

            tab.close()
            tab.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()

    def test_02b_notetaker_page_seek_wiring(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p1_nt_seek_") as td:
            td_obj = MagicMock()
            td_obj.name = td
            ctrl = _create_controller(td_obj)
            page = None
            try:
                page = NotetakerPage(ctrl)
                with patch.object(page.media_player, "setPosition") as mock_set_pos:
                    page.studio.tab_transcript.seek_requested.emit(37.5)
                    mock_set_pos.assert_called_once_with(37500)
            finally:
                _shutdown_controller(ctrl, page)

    # ------------------------------------------------------------------
    # 3. Debounced Searches Across HomePage, CalendarPage, NotetakerPage, CommandPalette
    # ------------------------------------------------------------------
    def test_03_debounced_searches(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p1_deb_") as td:
            td_obj = MagicMock()
            td_obj.name = td
            ctrl = _create_controller(td_obj)
            home = None
            notetaker = None
            cal = None
            palette = None
            try:
                ctrl.db.save_dictation("hello standup note", "Hello standup note.", duration_sec=1.5)
                ctrl.db.create_meeting(title="Roadmap Sync")

                # A. HomePage debounced feed search
                home = HomePage(ctrl)
                feed_calls = 0
                orig_refresh_feed = home.refresh_feed

                def counted_refresh_feed():
                    nonlocal feed_calls
                    feed_calls += 1
                    orig_refresh_feed()

                home.refresh_feed = counted_refresh_feed
                # Rebind timer to counted wrapper to mirror user signal
                home._feed_search_timer.timeout.disconnect()
                home._feed_search_timer.timeout.connect(counted_refresh_feed)

                home.txt_feed_search.setText("h")
                home.txt_feed_search.setText("he")
                home.txt_feed_search.setText("hello")
                self.app.processEvents()
                self.assertEqual(feed_calls, 0, "HomePage search should not refresh synchronously per keystroke")
                self.assertTrue(_wait_until(lambda: feed_calls == 1, timeout_sec=1.5))
                home.close()

                # B. NotetakerPage debounced meeting search
                notetaker = NotetakerPage(ctrl)
                nt_calls = 0
                orig_nt_refresh = notetaker.refresh_meetings_list

                def counted_nt_refresh():
                    nonlocal nt_calls
                    nt_calls += 1
                    orig_nt_refresh()

                notetaker._search_debounce_timer.timeout.disconnect()
                notetaker._search_debounce_timer.timeout.connect(counted_nt_refresh)

                notetaker.txt_search.setText("r")
                notetaker.txt_search.setText("ro")
                notetaker.txt_search.setText("roadmap")
                self.app.processEvents()
                self.assertEqual(nt_calls, 0)
                self.assertTrue(_wait_until(lambda: nt_calls == 1, timeout_sec=1.5))
                notetaker.close()

                # C. CalendarPage debounced search updates meetings panel ONLY (not accounts/KPIs/pulse)
                cal = CalendarPage(ctrl)
                with patch.object(cal, "refresh", wraps=cal.refresh) as mock_full_refresh, \
                     patch.object(cal, "_render_accounts_panel", wraps=cal._render_accounts_panel) as mock_accts, \
                     patch.object(cal, "_render_kpis_and_spotlight", wraps=cal._render_kpis_and_spotlight) as mock_kpis, \
                     patch.object(cal, "_render_daily_pulse", wraps=cal._render_daily_pulse) as mock_pulse, \
                     patch.object(cal, "_render_meetings_panel", wraps=cal._render_meetings_panel) as mock_mtgs:
                    cal.txt_search.setText("s")
                    cal.txt_search.setText("sp")
                    cal.txt_search.setText("sprint")
                    self.app.processEvents()
                    self.assertEqual(mock_mtgs.call_count, 0)
                    self.assertTrue(_wait_until(lambda: mock_mtgs.call_count == 1, timeout_sec=1.5))
                    self.assertEqual(mock_full_refresh.call_count, 0)
                    self.assertEqual(mock_accts.call_count, 0)
                    self.assertEqual(mock_kpis.call_count, 0)
                    self.assertEqual(mock_pulse.call_count, 0)
                cal._tick_timer.stop()
                cal.close()

                # D. VoxCommandPaletteDialog synchronous static filter + debounced DB query
                cmds = [
                    PaletteCommand(id="home", title="Home Workspace", description="Go home", category="Nav", shortcut="Ctrl+1"),
                    PaletteCommand(id="settings", title="Settings", description="Open prefs", category="Nav", shortcut="Ctrl+,"),
                ]
                palette = VoxCommandPaletteDialog(commands=cmds, controller=ctrl)
                with patch.object(palette, "_search_database_matches", wraps=palette._search_database_matches) as mock_db_search:
                    palette.txt_search.setText("Ro")
                    palette.txt_search.setText("Road")
                    self.app.processEvents()
                    self.assertEqual(mock_db_search.call_count, 0, "DB search must be debounced on keystroke")
                    palette.txt_search.setText("Roadmap")
                    self.assertTrue(_wait_until(lambda: mock_db_search.call_count == 1, timeout_sec=1.5))
                    titles = [
                        palette._filtered_commands[i].title for i in range(len(palette._filtered_commands))
                    ]
                    self.assertIn("Roadmap Sync", titles)
                palette.close()
            finally:
                _shutdown_controller(ctrl, home, notetaker, cal, palette)

    # ------------------------------------------------------------------
    # 4. VoxNotetakerDrawer Full Reliability Checks
    # ------------------------------------------------------------------
    def test_04_drawer_buttons_dark_mode_scroll_notes_slash_and_async_ask(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p1_drw_") as td:
            td_obj = MagicMock()
            td_obj.name = td
            ctrl = _create_controller(td_obj)
            drawer = None
            empty_drawer = None
            try:
                m1 = ctrl.db.create_meeting(title="Product Launch")
                m2 = ctrl.db.create_meeting(title="Finance Sync")
                folder_id = ctrl.db.create_folder(name="Engineering", folder_type="personal")

                seg_ids = []
                for i in range(30):
                    sid = ctrl.db.add_transcript_segment(
                        meeting_id=m1,
                        segment_index=i,
                        start_time=float(i * 15),
                        end_time=float(i * 15 + 10),
                        speaker=f"Speaker {(i % 2) + 1}",
                        text=f"Detailed transcript utterance number {i} for scroll and playback verification.",
                    )
                    seg_ids.append(sid)

                ctrl.db.update_meeting(m1, summary="We reviewed Q4 goals. Launch stays on track.")

                studio_calls = 0

                def _open_studio():
                    nonlocal studio_calls
                    studio_calls += 1

                drawer = VoxNotetakerDrawer(ctrl, on_open_meeting_studio=_open_studio)
                drawer.resize(520, 480)
                drawer.show()
                drawer._display_meeting(m1)
                self.app.processEvents()

                # A. Hardcoded placeholder strings are completely gone
                forbidden = ("HR Connect Session", "Role and Satisfaction", "Tools & Action Items")
                all_html_text = drawer.txt_title.text() + drawer.summary_browser.toHtml() + drawer.tx_browser.toHtml()
                for bad in forbidden:
                    self.assertNotIn(bad, all_html_text)
                self.assertEqual(drawer.lbl_avatar_pill.text(), "PL")

                # Empty drawer initialization must not carry "MH" or sample title
                empty_drawer = VoxNotetakerDrawer(ctrl)
                self.assertNotEqual(empty_drawer.lbl_avatar_pill.text(), "MH")
                empty_drawer.close()

                # B. btn_folder_pill assigns folder in SQLite
                def _pick_eng_folder(menu_self, _pos=None):
                    for act in menu_self.actions():
                        if "Engineering" in act.text():
                            return act
                    return None

                with patch("PyQt6.QtWidgets.QMenu.exec", _pick_eng_folder):
                    drawer._on_folder_pill_clicked()
                m1_after_folder = ctrl.db.get_meeting(m1)
                self.assertEqual(m1_after_folder["folder_id"], folder_id)
                self.assertEqual(drawer.btn_folder_pill.text(), "📁 Engineering")

                # C. btn_more and btn_share menus work & invoke actions cleanly
                def _pick_studio_action(menu_self, _pos=None):
                    for act in menu_self.actions():
                        if "Open in Notetaker Studio" in act.text():
                            return act
                    return None

                with patch("PyQt6.QtWidgets.QMenu.exec", _pick_studio_action):
                    drawer._on_more_clicked()
                    drawer._on_share_clicked()
                self.assertEqual(studio_calls, 2)

                # D. seek: and play: anchors emit seek_requested(meeting_id, seconds)
                seeks = []
                drawer.seek_requested.connect(lambda mid, sec: seeks.append((mid, sec)))
                drawer._on_anchor_clicked(QUrl("seek:42.5"))
                drawer._on_anchor_clicked(QUrl(f"play:{seg_ids[2]}"))
                self.assertEqual(seeks, [(m1, 42.5), (m1, 30.0)])

                # E. Dark mode removes all hardcoded light cream/green/charcoal tokens
                drawer.apply_theme("dark")
                drawer._on_ask_answer_received("Any risks?", "None reported.")
                dark_html = (drawer.tx_browser.toHtml() + drawer.summary_browser.toHtml()).lower()
                for light_hex in ("#f6f5ef", "#faf8f3", "#f0fdf4", "#1c1917"):
                    self.assertNotIn(light_hex, dark_html, f"Found light hex {light_hex} in dark drawer HTML")

                # F. Scroll lock + Jump to Live button
                drawer._switch_section(1)
                self.app.processEvents()
                sb = drawer.tx_browser.verticalScrollBar()
                self.assertGreater(sb.maximum(), 50, "Transcript should exceed viewport height for scroll lock test")
                sb.setValue(0)
                self.app.processEvents()
                self.assertTrue(drawer.btn_jump_live.isVisible())

                ctrl._active_meeting_id = m1
                drawer._on_progressive_segment(
                    m1,
                    {"speaker": "Speaker 1", "start_time": 999.0, "end_time": 1002.0, "text": "New live line appended."},
                )
                self.app.processEvents()
                self.assertEqual(sb.value(), 0, "Scroll Lock must preserve user's scroll position instead of jumping to bottom")
                self.assertTrue(drawer.btn_jump_live.isVisible())

                drawer._jump_to_live_bottom()
                self.app.processEvents()
                self.assertEqual(sb.value(), sb.maximum())
                self.assertFalse(drawer.btn_jump_live.isVisible())

                # G. Debounced notes auto-save + flush on switch
                drawer._switch_section(0)
                drawer.notes_editor.setPlainText("Private reflection during Product Launch.")
                self.assertTrue(drawer._notes_save_timer.isActive())
                self.assertTrue(
                    _wait_until(lambda: not drawer._notes_dirty, timeout_sec=1.5),
                    "Drawer notes should auto-save cleanly",
                )
                self.assertEqual(ctrl.db.get_meeting(m1)["notes"], "Private reflection during Product Launch.")

                drawer.notes_editor.setPlainText("Unsaved note flushed on meeting switch.")
                self.assertTrue(drawer._notes_save_timer.isActive())
                drawer._display_meeting(m2)
                self.assertFalse(drawer._notes_save_timer.isActive())
                self.assertEqual(ctrl.db.get_meeting(m1)["notes"], "Unsaved note flushed on meeting switch.")

                # H. '/' Slash skills bar + worker-thread Ask VOX
                drawer._display_meeting(m1)
                drawer.txt_ask_anything.setText("/")
                self.app.processEvents()
                self.assertTrue(drawer.slash_skills_bar.isVisible())
                self.assertEqual(
                    sum(1 for b in drawer._slash_buttons.values() if b.isVisible()),
                    len(drawer._slash_buttons),
                )

                drawer.txt_ask_anything.setText("/act")
                self.app.processEvents()
                visible_cmds = [k for k, b in drawer._slash_buttons.items() if b.isVisible()]
                self.assertEqual(visible_cmds, ["/actions"])

                gui_tid = threading.get_ident()
                worker_tids = []

                def fake_query(prompt: str, meeting_id=None):
                    worker_tids.append(threading.get_ident())
                    time.sleep(0.03)
                    return {"answer": f"Async answer for {meeting_id}: action items verified."}

                with patch.object(ctrl, "query_meeting_memory", side_effect=fake_query):
                    drawer._activate_slash_skill("/actions")
                    ok = _wait_until(
                        lambda: "Async answer for" in drawer.summary_browser.toPlainText(),
                        timeout_sec=2.0,
                    )
                    self.assertTrue(ok, "Ask VOX worker response did not render into summary_browser")
                    self.assertEqual(len(worker_tids), 1)
                    self.assertNotEqual(worker_tids[0], gui_tid, "query_meeting_memory must run off the GUI thread")

                drawer.close()
            finally:
                _shutdown_controller(ctrl, drawer, empty_drawer)


if __name__ == "__main__":
    unittest.main()
