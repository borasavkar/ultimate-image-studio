# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('icon.ico', '.'), ('icon.png', '.')]
binaries = []
hiddenimports = []

# Bundle these packages in full (data files, binaries, hidden imports).
# numpy/scipy/skimage are collected explicitly: PyInstaller can miss numpy 2.x's
# `numpy._core` data dir, which breaks rembg/onnxruntime at runtime.
for pkg in ('customtkinter', 'rembg', 'onnxruntime', 'pooch', 'pymatting',
            'numpy', 'scipy', 'skimage'):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        # Optional AI packages may be absent in a "lite" build — skip them.
        pass


a = Analysis(
    ['ImageStudio.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='UltimateImageStudio',
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
    icon=['icon.ico'],
)
