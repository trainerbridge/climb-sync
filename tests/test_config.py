from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from climb_sync.config import Config, is_valid_ipv4, load_config, save_config
from climb_sync.grade.source import S4Z_URL

SCRATCH = Path(".tmp/test-config")


def test_load_config_rejects_invalid_values():
    path = SCRATCH / "invalid-values.toml"
    path.write_text(
        """
[kickr]
ip = "999.1.1.1"

[s4z]
url = "file:///tmp/not-a-websocket"

[logging]
level = "TRACE"
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg == Config()


def test_save_config_escapes_toml_strings():
    path = SCRATCH / "escaped-strings.toml"
    url = 'ws://localhost:1080/api/ws/events?name="quoted"'

    save_config(Config(kickr_ip="192.168.1.20", s4z_url=url, log_level="debug"), path)

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert raw["kickr"]["ip"] == "192.168.1.20"
    assert raw["s4z"]["url"] == url
    assert raw["logging"]["level"] == "DEBUG"


@pytest.mark.parametrize(
    "cfg",
    [
        Config(kickr_ip="not-an-ip"),
        Config(s4z_url="http://localhost:1080/api/ws/events"),
        Config(log_level="TRACE"),
    ],
)
def test_save_config_rejects_invalid_values(cfg):
    with pytest.raises(ValueError):
        save_config(cfg, SCRATCH / "rejected-values.toml")


def test_missing_config_uses_defaults():
    assert load_config(SCRATCH / "missing.toml").s4z_url == S4Z_URL


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("192.168.1.20", True),
        ("127.0.0.1", True),
        ("999.1.1.1", False),
        ("localhost", False),
        ("192.168.1", False),
        (None, False),
    ],
)
def test_is_valid_ipv4(value, expected):
    assert is_valid_ipv4(value) is expected
