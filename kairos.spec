# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Kairos Agent macOS app (Universal Binary)

import sys
from pathlib import Path

ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / 'app.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Bundle the sync code so it can be copied to ~/.kairos-agent/sync/
        (str(ROOT / 'sync'), 'sync'),
    ],
    hiddenimports=[
        'rumps',
        'tigeropen',
        'tigeropen.tiger_open_config',
        'tigeropen.trade.trade_client',
        'pandas',
        'dotenv',
        'certifi',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['keyring', 'keyring.backends', 'keyring.backends.macOS'],
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
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch=None,  # Native arch per build job
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

app = BUNDLE(
    coll,
    name='Kairos.app',
    icon=None,
    bundle_identifier='dev.kairos.agent',
    info_plist={
        'LSUIElement': True,           # Hide from Dock (menubar only)
        'NSHighResolutionCapable': True,
        'CFBundleShortVersionString': '1.3.0',
    },
)