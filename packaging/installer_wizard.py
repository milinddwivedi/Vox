"""
VOX Professional Windows Setup Wizard & Uninstaller GUI (PyQt6).

Provides a modern, Google-Workspace/Windows-11 clean 4-stage installation wizard:
1. Welcome & System Compatibility Check
2. Destination Folder & Setup Options (Shortcuts, Auto-Start, Model Provisioning)
3. Live Progress & Model Provisioning / Verification (real bytes, MB/s, Cancel, Retry)
4. Completion & Automatic VOX Launch (selected by default)

Also provides a clean Uninstaller dialog (`--uninstall`) that preserves user data by default.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


def _find_asset(rel_name: str) -> Optional[Path]:
    meipass = getattr(sys, "_MEIPASS", None)
    bases = []
    if meipass:
        bases.append(Path(meipass))
    if getattr(sys, "frozen", False):
        bases.append(Path(sys.executable).resolve().parent)
    bases.append(Path(__file__).resolve().parent.parent)
    for b in bases:
        cand = b / "assets" / rel_name
        if cand.exists():
            return cand
    return None


def _load_wizard_font(app: QApplication) -> QFont:
    ttf = _find_asset("fonts/GoogleSans.ttf")
    family = "Segoe UI"
    if ttf is not None and ttf.is_file():
        try:
            fid = QFontDatabase.addApplicationFont(str(ttf))
            if fid >= 0:
                fams = QFontDatabase.applicationFontFamilies(fid)
                if fams:
                    family = fams[0]
        except Exception:
            pass
    font = QFont(family, 10)
    app.setFont(font)
    return font


def _default_user_data_preview(install_dir: Path) -> tuple[Path, bool]:
    legacy_db = install_dir / "data" / "voxlocal.db"
    legacy_cfg = install_dir / "data" / "config.json"
    has_legacy = legacy_db.is_file() or legacy_cfg.is_file()
    local_app = os.environ.get("LOCALAPPDATA", "").strip()
    target = Path(local_app) / "VOX" if local_app else (Path.home() / "AppData" / "Local" / "VOX")
    return target, has_legacy


def _compute_wizard_model_plan(install_dir: Path, ram_total_gb: float) -> Dict[str, Any]:
    """Resolve authoritative model selection and provisioning bytes for the host hardware."""
    try:
        from voxlocal.engine.model_manifest import calculate_model_selection_summary
        from voxlocal.engine.model_provisioner import ModelProvisioner

        data_root, _ = _default_user_data_preview(install_dir)
        models_dir = data_root / "models"
        summary = calculate_model_selection_summary(ram_total_gb=ram_total_gb)
        prov = ModelProvisioner(models_dir=models_dir)
        plan = prov.estimate_provisioning_plan(ram_total_gb=ram_total_gb)
        return {**summary, **plan}
    except Exception:
        use_small = ram_total_gb < 12.0
        total_b = 6_214_904_922 if use_small else 7_350_358_533
        return {
            "dictation_model_id": "dictation_whisper_small" if use_small else "dictation_whisper_turbo",
            "dictation_display_name": (
                "Whisper Small (CTranslate2 INT8 — Lightweight Laptop Mode)"
                if use_small
                else "Whisper Large-v3-Turbo (CTranslate2 INT8 — Voice Dictation)"
            ),
            "selected_count": 5,
            "total_selected_bytes": total_b,
            "total_selected_gb_decimal": round(total_b / 1e9, 2),
            "already_present_bytes": 0,
            "download_needed_bytes": total_b,
            "download_needed_gb_decimal": round(total_b / 1e9, 2),
            "required_free_disk_bytes": total_b + 2_200_000_000,
            "required_free_disk_gb": round((total_b + 2_200_000_000) / 1e9, 1),
            "sufficient_disk_space": True,
        }


class _InstallWorkerThread(QThread):
    """Background worker driving actual extraction, model provisioning, verification, and shortcuts."""

    stage_changed = pyqtSignal(int, str, str)  # overall_pct, stage_title, detail
    detail_progress = pyqtSignal(int, int, str)  # value, max_val, label
    log_emitted = pyqtSignal(str)
    finished_result = pyqtSignal(int, str, dict)  # rc, message, summary

    def __init__(
        self,
        *,
        install_dir: Path,
        create_desktop: bool,
        create_start_menu: bool,
        enable_autostart: bool,
        provision_models: bool,
        cancel_event: threading.Event,
        runner_fn: Callable[..., int],
    ) -> None:
        super().__init__()
        self.install_dir = install_dir
        self.create_desktop = create_desktop
        self.create_start_menu = create_start_menu
        self.enable_autostart = enable_autostart
        self.provision_models = provision_models
        self.cancel_event = cancel_event
        self.runner_fn = runner_fn

    def run(self) -> None:
        summary: Dict[str, Any] = {}
        try:
            def _on_progress(overall_pct: int, stage: str, detail: str, cur_b: int = 0, tot_b: int = 0) -> None:
                self.stage_changed.emit(int(overall_pct), str(stage), str(detail))
                if tot_b > 0:
                    pct = int(min(100, (cur_b * 100) // tot_b))
                    self.detail_progress.emit(pct, 100, str(detail))
                elif cur_b < 0:
                    self.detail_progress.emit(0, 0, str(detail))
                else:
                    self.detail_progress.emit(int(overall_pct), 100, str(detail))

            rc = self.runner_fn(
                silent=True,
                install_dir_override=self.install_dir,
                no_launch=True,
                provision_models=self.provision_models,
                create_desktop=self.create_desktop,
                create_start_menu=self.create_start_menu,
                enable_autostart=self.enable_autostart,
                register_uninstall=True,
                cancel_event=self.cancel_event,
                progress_cb=_on_progress,
                log_cb=lambda line: self.log_emitted.emit(str(line)),
                summary_out=summary,
            )
            if self.cancel_event.is_set() or rc == 130:
                self.finished_result.emit(130, "Installation cancelled safely. Temporary files were cleaned up.", summary)
            elif rc == 0:
                self.stage_changed.emit(100, "Installation Complete", "VOX is verified and ready to use.")
                self.detail_progress.emit(100, 100, "All checks passed")
                self.finished_result.emit(0, "VOX has been installed successfully.", summary)
            else:
                err = str(summary.get("error") or f"Installer exited with code {rc}")
                self.finished_result.emit(int(rc), err, summary)
        except Exception as exc:
            self.finished_result.emit(1, f"{type(exc).__name__}: {exc}", summary)


class VoxSetupWizard(QDialog):
    """Modern interactive Windows Setup Wizard for VOX v2.1."""

    STAGES_LIST = [
        "Initializing Setup & system checks",
        "Backing up & preserving existing data",
        "Extracting VOX application & runtimes",
        "Preparing & downloading required AI models",
        "Verifying model integrity (SHA-256)",
        "Running local installation health checks",
        "Creating shortcuts & completing setup",
    ]

    def __init__(
        self,
        *,
        version: str,
        default_install_dir: Path,
        runner_fn: Callable[..., int],
        hardware_info: Optional[Dict[str, Any]] = None,
        screenshot_dir: Optional[Path] = None,
    ) -> None:
        super().__init__()
        self.version = version
        self.default_install_dir = Path(default_install_dir)
        self.runner_fn = runner_fn
        self.hardware_info = hardware_info or self._probe_hardware_lightweight()
        self.screenshot_dir = screenshot_dir
        self._cancel_event = threading.Event()
        self._worker: Optional[_InstallWorkerThread] = None
        self._install_rc: Optional[int] = None
        self._launched_app: bool = False

        self.setObjectName("voxSetupWizard")
        self.setWindowTitle(f"VOX Setup — v{self.version}")
        self.setMinimumSize(680, 520)
        self.resize(720, 560)
        ico = _find_asset("favicon.ico")
        if ico is not None:
            self.setWindowIcon(QIcon(str(ico)))

        self._apply_styles()
        self._build_ui()
        self._update_disk_preview()

    @staticmethod
    def _probe_hardware_lightweight() -> Dict[str, Any]:
        import platform

        is_64 = sys.maxsize > 2**32
        threads = os.cpu_count() or 4
        ram_gb = 16.0
        try:
            import ctypes
            from ctypes import wintypes

            class _MEM(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            st = _MEM()
            st.dwLength = ctypes.sizeof(_MEM)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                ram_gb = round(st.ullTotalPhys / (1024**3), 1)
        except Exception:
            pass
        return {
            "os": f"Windows {platform.release()} (64-bit)" if is_64 else "Windows (32-bit unsupported)",
            "cpu_threads": threads,
            "ram_total_gb": ram_gb,
            "compatible": is_64 and ram_gb >= 6.0,
        }

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QDialog#voxSetupWizard { background-color: #FFFFFF; color: #202124; }
            QDialog#voxSetupWizard QLabel { color: #202124; background: transparent; }
            QDialog#voxSetupWizard QCheckBox { color: #202124; background: transparent; spacing: 8px; }
            QFrame#wizardHeader { background-color: #F8F9FA; border-bottom: 1px solid #E0E3E7; }
            QFrame#wizardCard { background-color: #F8F9FA; border: 1px solid #E0E3E7; border-radius: 12px; }
            QFrame#wizardFooter { background-color: #FFFFFF; border-top: 1px solid #E0E3E7; }
            QLabel#titleLbl { font-size: 20px; font-weight: 700; color: #202124; }
            QLabel#subtitleLbl { font-size: 12.5px; color: #5F6368; }
            QLabel#sectionLbl { font-size: 13px; font-weight: 600; color: #202124; }
            QLineEdit { border: 1px solid #DADCE0; border-radius: 8px; padding: 7px 10px; background: #FFFFFF; color: #202124; }
            QPushButton { border: 1px solid #DADCE0; border-radius: 8px; padding: 8px 16px; background: #FFFFFF; color: #202124; font-weight: 600; }
            QPushButton:hover { background: #F1F3F4; }
            QPushButton#primaryBtn { background: #1967D2; color: #FFFFFF; border: 1px solid #1967D2; }
            QPushButton#primaryBtn:hover { background: #1558B0; }
            QPushButton#primaryBtn:disabled { background: #AECBFA; border-color: #AECBFA; color: #FFFFFF; }
            QProgressBar { border: none; border-radius: 5px; background: #E8EAED; text-align: right; height: 10px; }
            QProgressBar::chunk { background-color: #1967D2; border-radius: 5px; }
            QPlainTextEdit { background: #F8F9FA; border: 1px solid #E0E3E7; border-radius: 8px; font-family: Consolas, monospace; font-size: 11px; color: #3C4043; }
            """
        )

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        hdr = QFrame()
        hdr.setObjectName("wizardHeader")
        h_lay = QHBoxLayout(hdr)
        h_lay.setContentsMargins(24, 16, 24, 16)
        h_lay.setSpacing(14)

        self.logo_lbl = QLabel()
        self.logo_lbl.setFixedSize(40, 40)
        logo_png = _find_asset("vox_logo_128.png")
        if logo_png is not None and logo_png.is_file():
            self.logo_lbl.setPixmap(QPixmap(str(logo_png)).scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        h_lay.addWidget(self.logo_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self.lbl_hdr_title = QLabel(f"VOX Voice Assistant Setup  ·  v{self.version}")
        self.lbl_hdr_title.setObjectName("titleLbl")
        self.lbl_hdr_sub = QLabel("100% Local AI Voice Typing & Meeting Intelligence for Windows")
        self.lbl_hdr_sub.setObjectName("subtitleLbl")
        title_col.addWidget(self.lbl_hdr_title)
        title_col.addWidget(self.lbl_hdr_sub)
        h_lay.addLayout(title_col, 1)

        self.lbl_step_badge = QLabel("Step 1 of 4")
        self.lbl_step_badge.setObjectName("subtitleLbl")
        h_lay.addWidget(self.lbl_step_badge)
        root.addWidget(hdr)

        # Body stack
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_page_welcome())
        self.stack.addWidget(self._build_page_options())
        self.stack.addWidget(self._build_page_progress())
        self.stack.addWidget(self._build_page_complete())
        root.addWidget(self.stack, 1)

        # Footer
        foot = QFrame()
        foot.setObjectName("wizardFooter")
        f_lay = QHBoxLayout(foot)
        f_lay.setContentsMargins(24, 14, 24, 14)
        f_lay.setSpacing(10)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        f_lay.addWidget(self.btn_cancel)

        f_lay.addStretch()

        self.btn_retry = QPushButton("Retry")
        self.btn_retry.setVisible(False)
        self.btn_retry.clicked.connect(self._start_installation)
        f_lay.addWidget(self.btn_retry)

        self.btn_back = QPushButton("Back")
        self.btn_back.clicked.connect(self._on_back_clicked)
        self.btn_back.setVisible(False)
        f_lay.addWidget(self.btn_back)

        self.btn_next = QPushButton("Continue →")
        self.btn_next.setObjectName("primaryBtn")
        self.btn_next.clicked.connect(self._on_next_clicked)
        f_lay.addWidget(self.btn_next)
        root.addWidget(foot)

    def _build_page_welcome(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(28, 22, 28, 18)
        lay.setSpacing(14)

        intro = QLabel(
            "Welcome to VOX Setup. This wizard installs VOX locally on your Windows PC, "
            "provisions and SHA-256 verifies all required offline speech and meeting AI models, "
            "and preserves any existing recordings, transcripts, and settings."
        )
        intro.setWordWrap(True)
        lay.addWidget(intro)

        card = QFrame()
        card.setObjectName("wizardCard")
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(16, 14, 16, 14)
        c_lay.setSpacing(8)
        hdr = QLabel("What will be set up for you automatically:")
        hdr.setObjectName("sectionLbl")
        c_lay.addWidget(hdr)
        ram_gb = float(self.hardware_info.get("ram_total_gb") or 16.0)
        plan = _compute_wizard_model_plan(self.default_install_dir, ram_gb)
        self._initial_model_plan = plan
        dict_desc = (
            "• Universal Voice Dictation (Whisper Small CT2 INT8 — Selected for <12 GB RAM)"
            if plan.get("dictation_model_id") == "dictation_whisper_small"
            else "• Universal Voice Dictation (Whisper Large-v3-Turbo CTranslate2 INT8)"
        )
        for line in (
            "• Zero-Python standalone VOX Desktop application & system tray integration",
            dict_desc,
            "• English Post-Call Meeting Transcription (Cohere Transcribe 2B INT8)",
            "• Local Speaker Diarization (PyAnnote + WeSpeaker) & Semantic Knowledge Search",
            "• Offline Meeting Intelligence & Ask VOX (Qwen3.5-4B GGUF via llama.cpp Vulkan/CPU)",
            f"• Selected 5 AI Models Total: {plan.get('total_selected_gb_decimal', 7.35):.2f} GB "
            f"({plan.get('download_needed_gb_decimal', 7.35):.2f} GB to download; cached models reused automatically)",
        ):
            lbl = QLabel(line)
            lbl.setWordWrap(True)
            c_lay.addWidget(lbl)
        lay.addWidget(card)

        compat_card = QFrame()
        compat_card.setObjectName("wizardCard")
        cc_lay = QVBoxLayout(compat_card)
        cc_lay.setContentsMargins(16, 12, 16, 12)
        cc_lay.setSpacing(4)
        cc_hdr = QLabel("System Compatibility Check")
        cc_hdr.setObjectName("sectionLbl")
        cc_lay.addWidget(cc_hdr)
        hw = self.hardware_info
        status_color = "#137333" if hw.get("compatible", True) else "#C5221F"
        self.lbl_compat = QLabel(
            f"<span style='color:{status_color};font-weight:700;'>"
            f"{'✓ Compatible' if hw.get('compatible', True) else '⚠ Below Recommended Specs'}</span> — "
            f"{hw.get('os')} · {hw.get('cpu_threads')} Threads · {hw.get('ram_total_gb')} GB RAM"
        )
        self.lbl_compat.setTextFormat(Qt.TextFormat.RichText)
        cc_lay.addWidget(self.lbl_compat)
        lay.addWidget(compat_card)
        lay.addStretch()
        return w

    def _build_page_options(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(28, 22, 28, 18)
        lay.setSpacing(14)

        lbl_dir = QLabel("Destination Application Folder")
        lbl_dir.setObjectName("sectionLbl")
        lay.addWidget(lbl_dir)

        row = QHBoxLayout()
        self.txt_install_dir = QLineEdit(str(self.default_install_dir))
        self.txt_install_dir.textChanged.connect(lambda *_: self._update_disk_preview())
        row.addWidget(self.txt_install_dir, 1)
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_install_dir)
        row.addWidget(btn_browse)
        lay.addLayout(row)

        self.lbl_data_path = QLabel("")
        self.lbl_data_path.setObjectName("subtitleLbl")
        self.lbl_data_path.setWordWrap(True)
        lay.addWidget(self.lbl_data_path)

        opts = QFrame()
        opts.setObjectName("wizardCard")
        o_lay = QVBoxLayout(opts)
        o_lay.setContentsMargins(16, 14, 16, 14)
        o_lay.setSpacing(10)

        plan = getattr(self, "_initial_model_plan", None) or _compute_wizard_model_plan(
            self.default_install_dir, float(self.hardware_info.get("ram_total_gb") or 16.0)
        )
        tot_gb = plan.get("total_selected_gb_decimal", 7.35)
        dl_gb = plan.get("download_needed_gb_decimal", tot_gb)
        cnt = plan.get("selected_count", 5)
        self.chk_provision_models = QCheckBox(
            f"Download && SHA-256 verify {cnt} selected local AI models "
            f"({dl_gb:.2f} GB to download / {tot_gb:.2f} GB total)"
        )
        self.chk_provision_models.setChecked(True)
        self.chk_provision_models.toggled.connect(lambda *_: self._update_disk_preview())
        o_lay.addWidget(self.chk_provision_models)

        self.chk_desktop_shortcut = QCheckBox("Create Desktop shortcut")
        self.chk_desktop_shortcut.setChecked(True)
        o_lay.addWidget(self.chk_desktop_shortcut)

        self.chk_start_menu = QCheckBox("Create Start Menu shortcuts")
        self.chk_start_menu.setChecked(True)
        o_lay.addWidget(self.chk_start_menu)

        self.chk_autostart = QCheckBox("Start VOX automatically when signing into Windows")
        self.chk_autostart.setChecked(True)
        o_lay.addWidget(self.chk_autostart)

        lay.addWidget(opts)

        self.lbl_disk_status = QLabel("")
        self.lbl_disk_status.setObjectName("subtitleLbl")
        lay.addWidget(self.lbl_disk_status)
        lay.addStretch()
        return w

    def _build_page_progress(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(28, 22, 28, 18)
        lay.setSpacing(12)

        self.lbl_prog_stage = QLabel("Preparing installation...")
        self.lbl_prog_stage.setObjectName("sectionLbl")
        lay.addWidget(self.lbl_prog_stage)

        self.bar_overall = QProgressBar()
        self.bar_overall.setRange(0, 100)
        self.bar_overall.setValue(0)
        self.bar_overall.setTextVisible(False)
        lay.addWidget(self.bar_overall)

        self.lbl_prog_detail = QLabel("Initializing...")
        self.lbl_prog_detail.setObjectName("subtitleLbl")
        lay.addWidget(self.lbl_prog_detail)

        self.bar_stage_detail = QProgressBar()
        self.bar_stage_detail.setRange(0, 100)
        self.bar_stage_detail.setValue(0)
        self.bar_stage_detail.setTextVisible(False)
        lay.addWidget(self.bar_stage_detail)

        self.txt_install_log = QPlainTextEdit()
        self.txt_install_log.setReadOnly(True)
        self.txt_install_log.setMaximumBlockCount(400)
        lay.addWidget(self.txt_install_log, 1)
        return w

    def _build_page_complete(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(28, 26, 28, 18)
        lay.setSpacing(14)

        self.lbl_done_title = QLabel("VOX Setup Complete")
        self.lbl_done_title.setObjectName("titleLbl")
        lay.addWidget(self.lbl_done_title)

        self.lbl_done_body = QLabel("")
        self.lbl_done_body.setWordWrap(True)
        lay.addWidget(self.lbl_done_body)

        self.chk_launch_now = QCheckBox("Launch VOX now")
        self.chk_launch_now.setChecked(True)
        lay.addWidget(self.chk_launch_now)
        lay.addStretch()
        return w

    def _browse_install_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose VOX Installation Folder", self.txt_install_dir.text())
        if chosen:
            self.txt_install_dir.setText(chosen)

    def _update_disk_preview(self) -> None:
        target = Path(self.txt_install_dir.text().strip() or str(self.default_install_dir))
        data_root, has_legacy = _default_user_data_preview(target)
        if has_legacy:
            self.lbl_data_path.setText(
                f"User Data & Models Location: {data_root} (existing data will migrate automatically & safely)"
            )
        else:
            self.lbl_data_path.setText(f"User Data & Models Location: {data_root} (separated from binaries)")

        ram_gb = float(self.hardware_info.get("ram_total_gb") or 16.0)
        plan = _compute_wizard_model_plan(target, ram_gb)
        req_gb = (
            float(plan.get("required_free_disk_gb") or 9.6)
            if self.chk_provision_models.isChecked()
            else 2.2
        )
        try:
            probe = target
            while not probe.exists() and probe.parent != probe:
                probe = probe.parent
            free_gb = shutil.disk_usage(str(probe)).free / 1_000_000_000.0
            enough = free_gb >= req_gb
            if enough:
                self.lbl_disk_status.setText(
                    f"Required free space: ~{req_gb:.1f} GB  ·  Available on target drive: {free_gb:.1f} GB"
                )
                self.lbl_disk_status.setStyleSheet("")
            else:
                self.lbl_disk_status.setText(
                    f"Insufficient disk space: ~{req_gb:.1f} GB required, only {free_gb:.1f} GB available."
                )
                self.lbl_disk_status.setStyleSheet("color: #C5221F; font-weight: 600;")
            if self.stack.currentIndex() == 1:
                self.btn_next.setEnabled(enough)
        except Exception:
            self.lbl_disk_status.setText("")

    def _go_to_page(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        self.lbl_step_badge.setText(f"Step {idx + 1} of 4")
        if idx == 0:
            self.btn_back.setVisible(False)
            self.btn_retry.setVisible(False)
            self.btn_cancel.setEnabled(True)
            self.btn_next.setText("Continue →")
            self.btn_next.setEnabled(True)
        elif idx == 1:
            self.btn_back.setVisible(True)
            self.btn_retry.setVisible(False)
            self.btn_cancel.setEnabled(True)
            self.btn_next.setText("Install VOX")
            self.btn_next.setEnabled(True)
            self._update_disk_preview()
        elif idx == 2:
            self.btn_back.setVisible(False)
            self.btn_retry.setVisible(False)
            self.btn_cancel.setEnabled(True)
            self.btn_next.setText("Installing...")
            self.btn_next.setEnabled(False)
        elif idx == 3:
            self.btn_back.setVisible(False)
            self.btn_retry.setVisible(False)
            self.btn_cancel.setVisible(False)
            self.btn_next.setText("Finish")
            self.btn_next.setEnabled(True)

    def _on_back_clicked(self) -> None:
        cur = self.stack.currentIndex()
        if cur == 1:
            self._go_to_page(0)

    def _on_next_clicked(self) -> None:
        cur = self.stack.currentIndex()
        if cur == 0:
            self._go_to_page(1)
        elif cur == 1:
            self._start_installation()
        elif cur == 3:
            if self._install_rc == 0 and self.chk_launch_now.isChecked():
                self._launch_installed_app()
            self.accept()

    def _on_cancel_clicked(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            ans = QMessageBox.question(
                self,
                "Cancel Installation",
                "VOX installation is in progress. Cancel safely and roll back incomplete changes?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
            self._cancel_event.set()
            self.btn_cancel.setEnabled(False)
            self.lbl_prog_stage.setText("Cancelling safely...")
        else:
            self._install_rc = 130
            self.reject()

    def _start_installation(self) -> None:
        target = Path(self.txt_install_dir.text().strip() or str(self.default_install_dir)).resolve()
        self._cancel_event.clear()
        self._go_to_page(2)
        self.txt_install_log.clear()
        self.bar_overall.setValue(2)
        self.bar_stage_detail.setValue(0)
        self._worker = _InstallWorkerThread(
            install_dir=target,
            create_desktop=self.chk_desktop_shortcut.isChecked(),
            create_start_menu=self.chk_start_menu.isChecked(),
            enable_autostart=self.chk_autostart.isChecked(),
            provision_models=self.chk_provision_models.isChecked(),
            cancel_event=self._cancel_event,
            runner_fn=self.runner_fn,
        )
        self._worker.stage_changed.connect(self._on_worker_stage)
        self._worker.detail_progress.connect(self._on_worker_detail)
        self._worker.log_emitted.connect(self.txt_install_log.appendPlainText)
        self._worker.finished_result.connect(self._on_worker_finished)
        self._worker.start()

    def _on_worker_stage(self, pct: int, stage: str, detail: str) -> None:
        self.bar_overall.setValue(max(0, min(100, int(pct))))
        self.lbl_prog_stage.setText(stage)
        self.lbl_prog_detail.setText(detail)

    def _on_worker_detail(self, val: int, max_v: int, label: str) -> None:
        self.bar_stage_detail.setRange(0, max_v)
        self.bar_stage_detail.setValue(val)
        if label:
            self.lbl_prog_detail.setText(label)

    def _on_worker_finished(self, rc: int, message: str, summary: Dict[str, Any]) -> None:
        self._install_rc = int(rc)
        if rc == 0:
            install_dir = Path(self.txt_install_dir.text().strip() or str(self.default_install_dir)).resolve()
            self.lbl_done_title.setText(f"VOX v{self.version} Installed Successfully")
            models_ok = bool(summary.get("models_provisioned", True))
            extra = (
                "All required local AI models and runtimes are verified and ready."
                if models_ok
                else "Application binaries installed. Missing models can be downloaded from Settings."
            )
            self.lbl_done_body.setText(
                f"Installed to:\n{install_dir}\n\n{extra}\n\n"
                "Your existing recordings, transcripts, knowledge, and settings were preserved."
            )
            self.chk_launch_now.setVisible(True)
            self.chk_launch_now.setChecked(True)
            self._go_to_page(3)
        elif rc == 130:
            self.lbl_prog_stage.setText("Installation Cancelled")
            self.lbl_prog_detail.setText(message)
            self.btn_retry.setVisible(True)
            self.btn_back.setVisible(True)
            self.btn_cancel.setEnabled(True)
        else:
            self.lbl_prog_stage.setText("Installation Failed")
            self.lbl_prog_detail.setText(message)
            self.btn_retry.setVisible(True)
            self.btn_back.setVisible(True)
            self.btn_cancel.setEnabled(True)

    def _launch_installed_app(self) -> None:
        install_dir = Path(self.txt_install_dir.text().strip() or str(self.default_install_dir)).resolve()
        target_exe = install_dir / "VoxLocal.exe"
        if target_exe.is_file():
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200) | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            subprocess.Popen([str(target_exe)], cwd=str(install_dir), creationflags=flags, close_fds=True)
            self._launched_app = True

    def capture_verification_screenshots(self, out_dir: Path) -> List[Path]:
        """Render and save key wizard pages offscreen/visible for automated visual verification."""
        out_dir.mkdir(parents=True, exist_ok=True)
        saved: List[Path] = []
        self.show()
        QApplication.processEvents()
        for idx, name in [(0, "installer_welcome.png"), (1, "installer_options.png"), (2, "installer_progress.png"), (3, "installer_complete.png")]:
            self._go_to_page(idx)
            if idx == 2:
                self._on_worker_stage(64, "Downloading required AI models...", "Cohere Transcribe 2B (INT8) — 1.8 / 2.7 GB (42.5 MB/s)")
                self._on_worker_detail(68, 100, "Downloading encoder.int8.onnx.data (68%)")
                self.txt_install_log.setPlainText(
                    "[1/7] Checking system compatibility...\n"
                    "[2/7] Preserving existing user database...\n"
                    "[3/7] Extracted VoxLocal.exe & _internal runtime\n"
                    "[4/7] Provisioning required local AI models...\n"
                )
            elif idx == 3:
                self.lbl_done_title.setText(f"VOX v{self.version} Installed Successfully")
                self.lbl_done_body.setText(
                    f"Installed to:\n{self.default_install_dir}\n\n"
                    "All required local AI models and runtimes are verified and ready."
                )
            QApplication.processEvents()
            p = out_dir / name
            self.grab().save(str(p))
            saved.append(p)
        return saved


class VoxUninstallDialog(QDialog):
    """Windows Uninstaller UI preserving user recordings, DBs, and models by default."""

    def __init__(self, *, install_dir: Path, version: str, uninstall_fn: Callable[[Path, bool], int]) -> None:
        super().__init__()
        self.install_dir = Path(install_dir)
        self.version = version
        self.uninstall_fn = uninstall_fn

        self.setWindowTitle(f"Uninstall VOX Voice Assistant (v{version})")
        self.setMinimumSize(480, 260)
        ico = _find_asset("favicon.ico")
        if ico is not None:
            self.setWindowIcon(QIcon(str(ico)))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        lbl_hdr = QLabel(f"Uninstall VOX Voice Assistant v{version}?")
        lbl_hdr.setFont(QFont("Google Sans", 14, QFont.Weight.Bold))
        lay.addWidget(lbl_hdr)

        lbl_info = QLabel(
            f"Application files in {self.install_dir} and shortcuts will be removed.\n"
            "Your recordings, transcripts, database, notes, and downloaded AI models are preserved by default."
        )
        lbl_info.setWordWrap(True)
        lay.addWidget(lbl_info)

        self.chk_purge_data = QCheckBox(
            "Also permanently delete my recordings, transcripts, settings, and AI models (cannot be undone)"
        )
        self.chk_purge_data.setChecked(False)
        lay.addWidget(self.chk_purge_data)

        lay.addStretch()
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_uninst = QPushButton("Uninstall VOX")
        btn_uninst.setStyleSheet("background:#C5221F; color:#FFFFFF; border:none; border-radius:8px; padding:8px 16px; font-weight:600;")
        btn_uninst.clicked.connect(self._on_uninstall)
        btn_row.addWidget(btn_uninst)
        lay.addLayout(btn_row)

    def _on_uninstall(self) -> None:
        purge = self.chk_purge_data.isChecked()
        if purge:
            ans = QMessageBox.warning(
                self,
                "Confirm Permanent Data Removal",
                "Are you sure you want to permanently delete all VOX recordings, transcripts, knowledge, and AI models?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        rc = self.uninstall_fn(self.install_dir, purge)
        if rc == 0:
            msg = "VOX application removed." + (" User data deleted." if purge else " Your recordings, database, and models were preserved.")
            QMessageBox.information(self, "Uninstall Complete", msg)
            self.accept()
        else:
            QMessageBox.critical(self, "Uninstall Error", f"Failed uninstalling cleanly (exit code {rc}).")


def run_gui_wizard(
    *,
    version: str,
    default_install_dir: Path,
    runner_fn: Callable[..., int],
    screenshot_dir: Optional[Path] = None,
) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    _load_wizard_font(app)
    wiz = VoxSetupWizard(
        version=version,
        default_install_dir=default_install_dir,
        runner_fn=runner_fn,
        screenshot_dir=screenshot_dir,
    )
    if screenshot_dir is not None:
        wiz.capture_verification_screenshots(screenshot_dir)
        return 0
    wiz.exec()
    return int(wiz._install_rc if wiz._install_rc is not None else 0)


def run_uninstall_gui(*, install_dir: Path, version: str, uninstall_fn: Callable[[Path, bool], int]) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    _load_wizard_font(app)
    dlg = VoxUninstallDialog(install_dir=install_dir, version=version, uninstall_fn=uninstall_fn)
    dlg.exec()
    return 0
