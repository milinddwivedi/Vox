# -*- mode: python ; coding: utf-8 -*-
import os
import importlib.util
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs

ROOT_DIR = Path(os.path.abspath(SPECPATH)) if 'SPECPATH' in globals() else Path(os.getcwd())

datas = []
binaries = []
datas += collect_data_files('faster_whisper')
datas += [
    ('extensions/vox_meet_tab_capture', 'extensions/vox_meet_tab_capture'),
    ('assets', 'assets'),
    ('licenses', 'licenses'),
    ('packaging/version.json', '.'),
    ('packaging/model_manifest.json', '.'),
    ('packaging/version.json', 'packaging'),
    ('packaging/model_manifest.json', 'packaging'),
]
if (ROOT_DIR / 'runtime' / 'llama_cpp').exists():
    datas.append(('runtime/llama_cpp', 'runtime/llama_cpp'))

# Explicitly include Silero VAD ONNX asset when present in site-packages
_fw_spec = importlib.util.find_spec('faster_whisper')
if _fw_spec and _fw_spec.origin:
    _vad_onnx = Path(_fw_spec.origin).resolve().parent / 'assets' / 'silero_vad_v6.onnx'
    if _vad_onnx.exists():
        datas.append((str(_vad_onnx), 'faster_whisper/assets'))

# Add av.libs DLLs for PyAV H.264 screen recorder (without duplicating to root)
_av_spec = importlib.util.find_spec('av')
if _av_spec and _av_spec.origin:
    av_libs_dir = Path(_av_spec.origin).resolve().parent.parent / 'av.libs'
    if av_libs_dir.exists():
        for dll in av_libs_dir.glob('*.dll'):
            binaries.append((str(dll), 'av.libs'))

binaries += collect_dynamic_libs('ctranslate2')
binaries += collect_dynamic_libs('onnxruntime')
try:
    binaries += collect_dynamic_libs('sherpa_onnx')
except Exception:
    pass

a = Analysis(
    ['main.py'],
    pathex=[str(ROOT_DIR / 'src'), str(ROOT_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        'voxlocal',
        'voxlocal.core.config',
        'voxlocal.core.controller',
        'voxlocal.core.diagnostics',
        'voxlocal.core.first_run_health',
        'voxlocal.core.task_manager',
        'voxlocal.core.logger',
        'voxlocal.core.hardware',
        'voxlocal.core.updater',
        'voxlocal.core.vocabulary_manager',
        'voxlocal.core.metering',
        'voxlocal.core.entitlement',
        'voxlocal.core.ed25519_rfc8032',
        'voxlocal.auth.desktop_auth',
        'voxlocal.ui.pro_upgrade_dialog',
        'voxlocal.calendar',
        'voxlocal.calendar.auth_service',
        'voxlocal.calendar.base',
        'voxlocal.calendar.calendar_manager',
        'voxlocal.calendar.graph_provider',
        'voxlocal.calendar.google_provider',
        'voxlocal.calendar.zoom_provider',
        'voxlocal.calendar.unified_engine',
        'voxlocal.calendar.ics_provider',
        'voxlocal.calendar.mapi_provider',
        'voxlocal.calendar.mock_provider',
        'voxlocal.extensions.mcp_server',
        'voxlocal.audio.devices',
        'voxlocal.audio.chimes',
        'voxlocal.audio.dictation_recorder',
        'voxlocal.audio.meeting_recorder',
        'voxlocal.audio.streaming_typer',
        'voxlocal.audio.call_detector',
        'voxlocal.engine.transcriber',
        'voxlocal.engine.model_manifest',
        'voxlocal.engine.model_provisioner',
        'voxlocal.engine.meeting_model_manager',
        'voxlocal.engine.meeting_asr_pipeline',
        'voxlocal.engine.postcall_worker',
        'voxlocal.engine.llama_server_engine',
        'voxlocal.engine.knowledge_store',
        'voxlocal.engine.completeness',
        'voxlocal.engine.dictation_processor',
        'voxlocal.engine.meeting_intelligence',
        'voxlocal.engine.llm_engine',
        'sherpa_onnx',
        'tokenizers',
        'voxlocal.storage.database',
        'voxlocal.storage.exporter',
        'voxlocal.video.screen_recorder',
        'voxlocal.video.meeting_capture',
        'voxlocal.ui.theme',
        'voxlocal.ui.icons',
        'voxlocal.ui.animations',
        'voxlocal.ui.components',
        'voxlocal.ui.floating_widget',
        'voxlocal.ui.floating_pill',
        'voxlocal.ui.calendar_page',
        'voxlocal.ui.calendar_connect_dialog',
        'voxlocal.ui.calendar_prompt',
        'voxlocal.ui.command_palette',
        'voxlocal.ui.meeting_studio',
        'voxlocal.ui.meeting_ui_helpers',
        'voxlocal.ui.overall_intelligence_view',
        'voxlocal.ui.call_consent_dialog',
        'voxlocal.ui.meeting_prompt_popup',
        'voxlocal.ui.main_window',
        'voxlocal.ui.hub_pages',
        'voxlocal.ui.notetaker_page',
        'voxlocal.ui.settings_dialog',
        'voxlocal.ui.scratchpad_window',
        'voxlocal.ui.notetaker_drawer',
        'voxlocal.ui.onboarding_wizard',
        'voxlocal.ui.tray_manager',
        'voxlocal.ui.video_share_dialog',
        'voxlocal.annotation',
        'voxlocal.annotation.engine',
        'voxlocal.annotation.toolbar',
        'voxlocal.annotation.overlay',
        'voxlocal.annotation.snipping',
        'voxlocal.annotation.markup_studio',
        'voxlocal.win32.clipboard',
        'voxlocal.win32.hotkeys',
        'voxlocal.win32.startup',
        'voxlocal.win32.text_inserter',
        'voxlocal.win32.window_focus',
        'pyaudiowpatch',
        'scipy.signal',
        'faster_whisper',
        'ctranslate2',
        'av',
        'PyQt6.QtMultimedia',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch',
        'transformers',
        'sympy',
        'triton',
        'torchaudio',
        'torchvision',
        'hypothesis',
        'pytest',
        'playwright',
        'PyQt6.QtWebEngineCore',
        'PyQt6.QtWebEngineWidgets',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VoxLocal',
    icon='assets/favicon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VoxLocal',
)
