# Releasing climb-sync

End-to-end flow for cutting a new `climb-sync.exe` and publishing it as a
GitHub release. All steps are manual; there is no CI.

## Prerequisites

- `git` configured with push access to the repo.
- `gh` CLI authenticated (`gh auth login`) — only needed for Option A below.
- Python 3.12+ on a Windows machine. The .exe is Windows-only; cross-build
  is not supported.
- Build deps installed:
  ```bat
  pip install -r requirements.txt
  pip install pyinstaller==6.19.0
  ```
- Optional: a Kickr + Sauce4Zwift on LAN for an on-bike sanity check before
  publishing.

## Release flow

### 1. Bump the version

The source of truth is `src/climb_sync/__init__.py`:

```python
__version__ = "0.3.0"   # bump per semver
```

Mirror the same value into `pyproject.toml`:

```toml
[project]
version = "0.3.0"
```

Both files MUST agree at release time. `__version__` is what
`scripts/build_exe.py` reads to name the release ZIP; `pyproject.toml`'s
`version` is what `pip install` and `pip show climb-sync` report.

Commit the bump:

```bash
git add src/climb_sync/__init__.py pyproject.toml
git commit -m "chore: bump version to v0.3.0"
```

### 2. Build the .exe and release ZIP

```bash
python scripts/build_exe.py
```

This script:

1. Cleans `dist/` and `build/`.
2. Runs `pyinstaller --clean --noconfirm climb_sync.spec`.
3. Smoke-runs `dist/climb-sync.exe --help` to prove hidden imports and the
   entry point are bundled correctly.
4. Stages `dist/climb-sync-v{version}.zip` containing `climb-sync.exe`,
   `README.txt`, and `LICENSE`.
5. Prints SHA-256 of both the .exe and the .zip.

Example output:

```text
Built: climb-sync.exe
  size  : 28.4 MB
  sha256: 9c3e...

Staged: climb-sync-v0.3.0.zip
  size  : 27.9 MB
  sha256: 7f2a...
```

Record the SHA-256 of the ZIP — it goes into the release notes for users who
want to verify the download.

### 3. Sanity-check on the bike (optional but recommended)

If a Kickr + Sauce4Zwift are available on LAN, run the packaged exe through
a real Zwift session before publishing:

1. Start Sauce4Zwift and Zwift; pair the Kickr over BLE in Zwift.
2. Launch `dist/climb-sync.exe`. The tray icon should reach **green**
   within ~10 seconds.
3. Start any Zwift ride or workout. Confirm the Climb tilts as the road
   grade changes. During an ERG workout segment, ERG power should hold
   while the Climb still tracks grade.
4. Right-click the tray icon → Exit. Check `%APPDATA%\climb-sync\logs\app.log`
   for any unexpected errors.

If anything fails, don't publish. Fix, rebuild, re-test.

### 4. Tag the release

```bash
git tag v0.3.0
git push --tags
```

### 5. Publish via GitHub release

#### Option A: gh CLI

```bash
gh release create v0.3.0 dist/climb-sync-v0.3.0.zip \
    --title "v0.3.0" \
    --notes "$(cat <<'EOF'
## Install
1. Download `climb-sync-v0.3.0.zip` below.
2. Unzip. You will see `climb-sync.exe` and `README.txt`.
3. Double-click `climb-sync.exe`. Windows SmartScreen will warn
   ("Windows protected your PC"). Click "More info" then "Run anyway".
   This is normal for unsigned indie tools.
4. The app icon appears in your system tray, next to the clock.
   Right-click for status, override KICKR IP, restart sync, or exit.

## Verify download
SHA-256 of climb-sync-v0.3.0.zip:
    <paste SHA-256 from build_exe.py output>

## Requirements
- Wahoo Kickr v6 trainer + Kickr Climb on the same WiFi network as your PC
- Sauce4Zwift running locally
- Zwift connected to the Kickr over BLE, not Direct Connect

## Logs
%APPDATA%\climb-sync\logs\app.log
EOF
)"
```

#### Option B: Web UI

1. Go to `https://github.com/<you>/climb-sync/releases/new`.
2. Choose the `v0.3.0` tag (or create it).
3. Title: `v0.3.0`.
4. Notes: paste the markdown body from Option A.
5. Attach `dist/climb-sync-v0.3.0.zip`.
6. Publish.

## Antivirus expectations

The .exe is unsigned. Code signing is a future improvement.

- **Windows SmartScreen:** Will show "Windows protected your PC" on first
  download. `README.txt` walks the user through "More info → Run anyway".
- **Windows Defender:** Should not quarantine the .exe. UPX is disabled in
  the spec to reduce false-positive rates, and the ZIP SHA-256 is published.
  If Defender does quarantine it, submit a false-positive report at
  https://www.microsoft.com/en-us/wdsi/filesubmission and consider switching
  to a `--onedir` PyInstaller build in a follow-up release.
- **Third-party AV:** If multiple users report quarantine, the fallback is
  `--onedir` distribution instead of a single .exe.

Users who want extra confidence can verify the SHA-256 from the release
notes:

```powershell
Get-FileHash -Algorithm SHA256 .\climb-sync-v0.3.0.zip
```

## Troubleshooting

- **`build_exe.py` fails with `ModuleNotFoundError: No module named
  'PyInstaller'`:** Install it: `pip install pyinstaller==6.19.0`.
- **`build_exe.py` succeeds but the .exe crashes on launch with
  `ModuleNotFoundError: ... zeroconf ...` (or similar):** A hidden import
  is missing from `climb_sync.spec`. Run
  `pyinstaller --debug=imports climb_sync.spec` and inspect
  `build/climb_sync/warn-climb_sync.txt` for the missing module. Add
  it to `hiddenimports` in the spec.
