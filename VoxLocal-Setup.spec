# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

_vox_exe = os.environ.get('VOXLOCAL_EXE_PATH', 'dist/VoxLocal/VoxLocal.exe')
_vox_internal = Path(os.environ.get('VOXLOCAL_INTERNAL_DIR', str(Path(_vox_exe).resolve().parent / '_internal')))

_setup_datas = [('src', 'src'), ('assets', 'assets')]
if _vox_internal.exists():
    for _pkg_dir in ('sherpa_onnx', 'pydantic', 'pydantic_core', 'safetensors', 'markupsafe', 'tokenizers'):
        _cand = _vox_internal / _pkg_dir
        if _cand.is_dir():
            _setup_datas.append((str(_cand), f'bundled_internal/{_pkg_dir}'))
    for _di in _vox_internal.glob('*.dist-info'):
        if any(_di.name.lower().startswith(p) for p in ('pydantic', 'markupsafe', 'sherpa', 'safetensors', 'tokenizers')):
            _setup_datas.append((str(_di), f'bundled_internal/{_di.name}'))
    _base_zip = _vox_internal / 'base_library.zip'
    if _base_zip.is_file():
        _setup_datas.append((str(_base_zip), 'bundled_internal'))

a = Analysis(
    ['packaging/vox_setup_installer.py'],
    pathex=[],
    binaries=[(_vox_exe, 'bundled_bin')],
    datas=_setup_datas,
    hiddenimports=[],
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
