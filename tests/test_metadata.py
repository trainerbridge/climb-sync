from __future__ import annotations

import tomllib
from pathlib import Path

import climb_sync


def test_pyproject_version_matches_runtime_version():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["version"] == climb_sync.__version__
