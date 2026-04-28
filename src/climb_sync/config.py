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

import ipaddress
import logging
import os
import tomllib
from dataclasses import dataclass, replace as _dc_replace
from pathlib import Path
from urllib.parse import urlparse

from .grade.source import S4Z_URL

logger = logging.getLogger(__name__)

APP_NAME: str = "climb-sync"
DEFAULT_LOG_LEVEL: str = "INFO"
VALID_LOG_LEVELS: frozenset[str] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)

_UNSET: object = object()


@dataclass(frozen=True)
class Config:
    """Resolved app config. Frozen so menu callbacks cannot mutate from any thread."""

    kickr_ip: str | None = None
    s4z_url: str = S4Z_URL
    log_level: str = DEFAULT_LOG_LEVEL

    def replace(
        self,
        *,
        kickr_ip: str | None = _UNSET,  # type: ignore[assignment]
        s4z_url: str = _UNSET,  # type: ignore[assignment]
        log_level: str = _UNSET,  # type: ignore[assignment]
    ) -> "Config":
        """Type-checked partial update. Misspelled fields fail at typecheck time."""
        update: dict[str, object] = {}
        if kickr_ip is not _UNSET:
            update["kickr_ip"] = kickr_ip
        if s4z_url is not _UNSET:
            update["s4z_url"] = s4z_url
        if log_level is not _UNSET:
            update["log_level"] = log_level
        return _dc_replace(self, **update)


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

    if kickr is not None and not is_valid_ipv4(kickr):
        logger.warning("config: ignoring invalid kickr.ip %r", kickr)
        kickr = None
    if not _looks_like_ws_url(s4z_url):
        logger.warning("config: ignoring invalid s4z.url %r", s4z_url)
        s4z_url = S4Z_URL
    log_level = str(log_level).upper()
    if log_level not in VALID_LOG_LEVELS:
        logger.warning("config: ignoring invalid logging.level %r", log_level)
        log_level = DEFAULT_LOG_LEVEL

    return Config(kickr_ip=kickr, s4z_url=s4z_url, log_level=log_level)


def save_config(cfg: Config, path: Path | None = None) -> None:
    """Hand-format TOML write; the three-key schema does not justify a dependency.

    Atomic: writes to a sibling .tmp and os.replace(), so a crash mid-write
    cannot truncate the live config file.
    """
    p = path or config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if cfg.kickr_ip is not None:
        if not is_valid_ipv4(cfg.kickr_ip):
            raise ValueError(f"invalid kickr_ip: {cfg.kickr_ip!r}")
        lines.append("[kickr]")
        lines.append(f"ip = {_toml_string(cfg.kickr_ip)}")
        lines.append("")
    if not _looks_like_ws_url(cfg.s4z_url):
        raise ValueError(f"invalid s4z_url: {cfg.s4z_url!r}")
    log_level = cfg.log_level.upper()
    if log_level not in VALID_LOG_LEVELS:
        raise ValueError(f"invalid log_level: {cfg.log_level!r}")
    lines.append("[s4z]")
    lines.append(f"url = {_toml_string(cfg.s4z_url)}")
    lines.append("")
    lines.append("[logging]")
    lines.append(f"level = {_toml_string(log_level)}")
    lines.append("")

    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(tmp, p)
    logger.info("config: wrote %s", p)


def is_valid_ipv4(s: object) -> bool:
    """Strict IPv4 check via stdlib ipaddress.

    Rejects non-strings, leading-zero octets (CVE-2021-29921 family),
    and anything else IPv4Address won't accept.
    """
    if not isinstance(s, str):
        return False
    try:
        ipaddress.IPv4Address(s)
    except ValueError:
        return False
    return True


def _looks_like_ws_url(s: object) -> bool:
    if not isinstance(s, str) or len(s) > 2048:
        return False
    parsed = urlparse(s)
    return parsed.scheme in {"ws", "wss"} and bool(parsed.netloc)


def _toml_string(value: str) -> str:
    """Format a TOML basic string using JSON-compatible escaping."""
    import json

    return json.dumps(value)
