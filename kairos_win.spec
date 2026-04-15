# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — Windows (x86_64)

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files
ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / 'app.py')],      # same entry point as macOS
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / 'sync'), 'sync'),
        *collect_data_files('moomoo'),
    ],
    hiddenimports=[
        'pystray',
        'pystray._win32',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'tigeropen',
        'tigeropen.tiger_open_config',
        'tigeropen.trade.trade_client',
        'pandas',
        'dotenv',
        'certifi',
        # Moomoo — imported inside try/except so PyInstaller misses it
        'moomoo',
        'moomoo.common',
        'moomoo.trade',
        'moomoo.quote',
        # ssl_patch utility
        'ssl_patch',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['rumps', 'tkinter', 'truststore'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Kairos',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
    name='Kairos',
)
