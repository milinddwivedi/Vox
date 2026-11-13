# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs

ROOT_DIR = Path('D:/Milind/Voice Assistant')

datas = []
binaries = []
datas += collect_data_files('faster_whisper')
datas += [
    ('D:\\Milind\\Voice Assistant\\.venv\\Lib\\site-packages\\faster_whisper\\assets\\silero_vad_v6.onnx', 'faster_whisper/assets'),
    ('extensions/vox_meet_tab_capture', 'extensions/vox_meet_tab_capture'),
    ('assets', 'assets'),
]

# Add av.libs DLLs for PyAV H.264 screen recorder (without duplicating to root)
av_libs_dir = Path('D:/Milind/Voice Assistant/.venv/Lib/site-packages/av.libs')
if av_libs_dir.exists():
    for dll in av_libs_dir.glob('*.dll'):
        binaries.append((str(dll), 'av.libs'))

# NVIDIA CUDA runtime DLLs are loaded at runtime from .venv/Lib/site-packages/nvidia to avoid 2GB duplication

binaries += collect_dynamic_libs('ctranslate2')
binaries += collect_dynamic_libs('onnxruntime')

a = Analysis(
    ['main.py'],
    pathex=[str(ROOT_DIR / 'src'), str(ROOT_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        'voxlocal',
        'voxlocal.core.config',
        'voxlocal.core.controller',
        'voxlocal.core.logger',
        'voxlocal.core.vocabulary_manager',
        'voxlocal.calendar',
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
        'voxlocal.engine.dictation_processor',
        'voxlocal.engine.meeting_intelligence',
        'voxlocal.engine.llm_engine',
        'tokenizers',
        'voxlocal.storage.database',
        'voxlocal.storage.exporter',
        'voxlocal.video.screen_recorder',
        'voxlocal.video.meeting_capture',
        'voxlocal.ui.theme',
        'voxlocal.ui.animations',
        'voxlocal.ui.components',
        'voxlocal.ui.floating_widget',
        'voxlocal.ui.floating_pill',
        'voxlocal.ui.calendar_connect_dialog',
        'voxlocal.ui.calendar_prompt',
        'voxlocal.ui.command_palette',
        'voxlocal.ui.meeting_studio',
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
    excludes=[],
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
