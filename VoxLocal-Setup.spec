# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

ROOT_DIR = Path(os.path.abspath(SPECPATH)) if 'SPECPATH' in globals() else Path(os.getcwd())

_setup_datas = [
    ('assets/favicon.ico', 'assets'),
    ('assets/vox_logo_128.png', 'assets'),
    ('assets/fonts/GoogleSans.ttf', 'assets/fonts'),
    ('assets/fonts/OFL.txt', 'assets/fonts'),
    ('licenses/THIRD_PARTY_NOTICES.md', 'licenses'),
    ('packaging/version.json', 'packaging'),
    ('packaging/model_manifest.json', 'packaging'),
    ('src/voxlocal/engine/model_manifest.py', 'src/voxlocal/engine'),
    ('src/voxlocal/engine/model_provisioner.py', 'src/voxlocal/engine'),
]

_payload_tar = ROOT_DIR / 'build' / 'app_payload.tar.xz'
if _payload_tar.is_file():
    _setup_datas.append((str(_payload_tar), 'bundled_payload'))
else:
    # Fallback incremental delta mode when built standalone
    _vox_exe = os.environ.get('VOXLOCAL_EXE_PATH', 'dist/VoxLocal/VoxLocal.exe')
    _vox_internal = Path(os.environ.get('VOXLOCAL_INTERNAL_DIR', str(Path(_vox_exe).resolve().parent / '_internal')))
    _setup_datas.extend([('src', 'src'), ('assets', 'assets')])
    if _vox_internal.exists():
        for _pkg_dir in ('sherpa_onnx', 'pydantic', 'pydantic_core', 'safetensors', 'markupsafe', 'tokenizers'):
            _cand = _vox_internal / _pkg_dir
            if _cand.is_dir():
                _setup_datas.append((str(_cand), f'bundled_internal/{_pkg_dir}'))

a = Analysis(
    ['packaging/vox_setup_installer.py'],
    pathex=[str(ROOT_DIR / 'packaging'), str(ROOT_DIR / 'src')],
    binaries=[],
    datas=_setup_datas,
    hiddenimports=[
        'installer_wizard',
        'voxlocal.engine.model_manifest',
        'voxlocal.engine.model_provisioner',
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
        'faster_whisper',
        'ctranslate2',
        'onnxruntime',
        'sherpa_onnx',
        'numpy',
        'scipy',
        'av',
        'PIL',
        'pytest',
        'hypothesis',
        'playwright',
        'PyQt6.QtMultimedia',
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
    a.binaries,
    a.datas,
    [],
    name='VoxLocal-Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/favicon.ico'],
)
