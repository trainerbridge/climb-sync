# climb_sync.spec - committed to repo; build via `pyinstaller climb_sync.spec`
# -*- mode: python ; coding: utf-8 -*-
#
# Phase 3, plan 03 - pyinstaller --onefile build config.
#
# Decision references:
#   D-11: --onefile single climb-sync.exe distributed via GitHub release ZIP.
#   D-12: silent first-run UX -> console=False so no shell window flashes on launch.
#   AV mitigation (RESEARCH.md "Antivirus mitigation strategy"):
#     upx=False - UPX compression dramatically increases AV false-positive rates
#                 (github.com/upx/upx/issues/711). Saves ~5MB; not worth AV friction.
#     icon='assets/app.ico' - proper Windows file icon (Explorer thumbnail).
#     console=False - --windowed; no console window on launch.
#
# Hidden imports (RESEARCH.md "Hidden import sources" lines 791-794):
#   zeroconf._utils.ipaddress, zeroconf._handlers, ifaddr - zeroconf dynamic imports
#     pyinstaller can't trace statically (pyinstaller-hooks-contrib issue #840).
#   websockets.legacy.client, websockets.legacy.protocol - recent websockets uses
#     dynamic dispatch for protocol modules; safer to include explicitly.
#
# Excludes (RESEARCH.md OQ-3 line 989):
#   bleak - Phase 1 BLE path is dead per CLAUDE.md FINAL; if any transitive import
#           still pulls it in, exclude saves ~5MB.
#   numpy, pandas - defensive (not in deps; harmless if not present).
#   pytest, pytest_asyncio - only in [dev] extra; exclude prevents accidental
#                            bundling if pyinstaller runs in a dev venv.

block_cipher = None

a = Analysis(
    ['src/climb_sync/__main__.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'zeroconf._utils.ipaddress',
        'zeroconf._handlers',
        'ifaddr',
        'websockets.legacy.client',
        'websockets.legacy.protocol',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'bleak',
        'numpy',
        'pandas',
        'pytest',
        'pytest_asyncio',
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='climb-sync',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # AV mitigation - see header comment
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,            # D-12 silent first-run - no shell window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/app.ico',
)
