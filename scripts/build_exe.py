#!/usr/bin/env python3
"""Build climb-sync.exe via pyinstaller and stage the GitHub release ZIP.

Phase 3, plan 03 (D-11). Recipe B from 03-RESEARCH.md, extended with:
  - SHA-256 hash printout for both artifacts (referenced from release notes)
  - Post-build smoke: dist/climb-sync.exe --help (proves bundle correctness
    without needing Tk/pystray runtime; fails fast on missing hidden imports)
  - Tolerance for missing LICENSE (project does not have one yet; skip with warning)

Usage:
    pip install -e ".[app,build,dev]"
    python scripts/build_exe.py

Output:
    dist/climb-sync.exe
    dist/climb-sync-v{__version__}.zip

The build is deterministic in the pyinstaller-determinism sense: same source,
same deps, and same flags produce the same .exe. subprocess uses list-form args
with no shell invocation; see threat T-03-03-04.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIST = REPO / "dist"
BUILD = REPO / "build"
SPEC = REPO / "climb_sync.spec"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _smoke_help(exe: Path) -> int:
    """Post-build smoke: --help must exit 0 with no Tk/pystray init."""
    print(f"\nPost-build smoke: {exe} --help")
    result = subprocess.run(
        [str(exe), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        print(f"  FAIL: --help exited {result.returncode}", file=sys.stderr)
        print(f"  stderr: {result.stderr}", file=sys.stderr)
        return result.returncode

    out = result.stdout
    required_flags = [
        "--smoke",
        "--verbose",
        "--ride-start-delay",
        "--simulate-outage-at",
        "--ip",
    ]
    missing = [flag for flag in required_flags if flag not in out]
    if missing:
        print(f"  FAIL: --help output missing flags: {missing}", file=sys.stderr)
        return 1

    print("  OK: --help printed all expected flags")
    return 0


def main() -> int:
    sys.path.insert(0, str(REPO / "src"))
    from climb_sync import __version__

    print(f"Building climb-sync v{__version__}")

    for directory in (DIST, BUILD):
        if directory.exists():
            print(f"Cleaning {directory}")
            shutil.rmtree(directory)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        str(SPEC),
    ]
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=REPO)

    exe = DIST / "climb-sync.exe"
    if not exe.exists():
        print(f"ERROR: expected output {exe} not found", file=sys.stderr)
        return 1

    rc = _smoke_help(exe)
    if rc != 0:
        return rc

    zip_path = DIST / f"climb-sync-v{__version__}.zip"
    readme = REPO / "README.txt"
    license_file = REPO / "LICENSE"
    if not readme.exists():
        print(f"ERROR: required {readme} not found", file=sys.stderr)
        return 1

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(exe, "climb-sync.exe")
        zf.write(readme, "README.txt")
        if license_file.exists():
            zf.write(license_file, "LICENSE")
        else:
            print(f"WARN: {license_file} not present; release ZIP will omit LICENSE.")

    exe_sha = _sha256(exe)
    zip_sha = _sha256(zip_path)
    exe_mb = exe.stat().st_size / 1024 / 1024
    zip_mb = zip_path.stat().st_size / 1024 / 1024

    print()
    print("=" * 70)
    print(f"Built: {exe.name}")
    print(f"  size  : {exe_mb:.1f} MB")
    print(f"  sha256: {exe_sha}")
    print()
    print(f"Staged: {zip_path.name}")
    print(f"  size  : {zip_mb:.1f} MB")
    print(f"  sha256: {zip_sha}")
    print("=" * 70)
    print()
    print("Next steps (see docs/RELEASING.md):")
    print(f"  git tag v{__version__} && git push --tags")
    print(f"  gh release create v{__version__} {zip_path} \\")
    print(f"      --title \"v{__version__} - ...\" \\")
    print(f"      --notes \"sha256: {zip_sha}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
