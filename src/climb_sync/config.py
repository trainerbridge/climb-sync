"""App config - TOML at %APPDATA%\\climb-sync\\config.toml.

Schema (all keys optional):
    [kickr]
    ip = "192.168.26.65"           # bypass mDNS if set

    [s4z]
    url = "ws://localhost:1080/api/ws/events"

    [logging]
    level = "INFO"                 # INFO | DEBUG

D-04: TOML format, tomllib (stdlib 3.11+) for read, hand-format for write.
D-09 lockfile path comes from this module too.
"""
from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from .grade.source import S4Z_URL

logger = logging.getLogger(__name__)

APP_NAME: str = "climb-sync"
DEFAULT_LOG_LEVEL: str = "INFO"


@dataclass(frozen=True)
class Config:
    """Resolved app config. Frozen so menu callbacks cannot mutate from any thread."""

    kickr_ip: str | None = None
    s4z_url: str = S4Z_URL
    log_level: str = DEFAULT_LOG_LEVEL

    def replace(self, **kwargs) -> "Config":
        """Return a copy with the given fields overridden."""
        return replace(self, **kwargs)


def appdata_dir() -> Path:
    """Resolve %APPDATA%\\climb-sync, creating it if missing."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata)
    else:
        base = Path.home() / "AppData" / "Roaming"
    target = base / APP_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def config_path() -> Path:
    return appdata_dir() / "config.toml"


def log_dir() -> Path:
    d = appdata_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def lock_path() -> Path:
    return appdata_dir() / "app.lock"


def load_config(path: Path | None = None) -> Config:
    """Read config.toml; missing file means defaults silently per D-04."""
    p = path or config_path()
    if not p.exists():
        logger.info("config: no file at %s - using defaults", p)
        return Config()
    try:
        with p.open("rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("config: failed to read %s (%s); using defaults", p, e)
        return Config()

    kickr = (raw.get("kickr") or {}).get("ip")
    s4z_url = (raw.get("s4z") or {}).get("url") or S4Z_URL
    log_level = (raw.get("logging") or {}).get("level") or DEFAULT_LOG_LEVEL

    if kickr is not None and not _looks_like_ip(kickr):
        logger.warning("config: ignoring invalid kickr.ip %r", kickr)
        kickr = None

    return Config(kickr_ip=kickr, s4z_url=s4z_url, log_level=log_level)


def save_config(cfg: Config, path: Path | None = None) -> None:
    """Hand-format TOML write; the three-key schema does not justify a dependency."""
    p = path or config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if cfg.kickr_ip is not None:
        lines.append("[kickr]")
        lines.append(f'ip = "{cfg.kickr_ip}"')
        lines.append("")
    lines.append("[s4z]")
    lines.append(f'url = "{cfg.s4z_url}"')
    lines.append("")
    lines.append("[logging]")
    lines.append(f'level = "{cfg.log_level}"')
    lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")
    logger.info("config: wrote %s", p)


def _looks_like_ip(s: str) -> bool:
    """Cheap IPv4 sanity check for config.toml and dialog-saved input."""
    if not s or len(s) > 15:
        return False
    parts = s.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit():
            return False
        n = int(p)
        if n < 0 or n > 255:
            return False
    return True
