from __future__ import annotations

import sys
from types import SimpleNamespace

from climb_sync.tray import app as tray_app


class _FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items):
        self.items = items


class _FakeMenuItem:
    def __init__(self, text, action, enabled=True):
        self.text = text
        self.action = action
        self.enabled = enabled


class _FakeIcon:
    def __init__(self, *, name, icon, title, menu):
        self.name = name
        self.icon = icon
        self.title = title
        self.menu = menu
        self.updated = 0
        self.stopped = False

    def update_menu(self):
        self.updated += 1

    def stop(self):
        self.stopped = True


class _FakeLoop:
    def __init__(self, *, connected_dircon: bool, attempt_count: int) -> None:
        self.status = {
            "connected_dircon": connected_dircon,
            "connected_s4z": True,
            "last_grade": None,
            "last_smoothed": None,
            "staleness": "fresh",
            "attempt_count": attempt_count,
        }


def test_tray_menu_reads_current_sync_loop(monkeypatch):
    fake_pystray = SimpleNamespace(
        Menu=_FakeMenu,
        MenuItem=_FakeMenuItem,
        Icon=_FakeIcon,
    )
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    monkeypatch.setattr(tray_app, "generate_icon", lambda color: color)

    current = _FakeLoop(connected_dircon=False, attempt_count=1)
    icon = tray_app.build_tray_icon(
        get_sync_loop=lambda: current,
        on_restart_sync=lambda: None,
        on_override_ip=lambda: None,
        on_exit=lambda: None,
        poll_interval_seconds=60.0,
    )
    try:
        dynamic_menu = icon.menu.items[0]()
        first_status_item = dynamic_menu.items[0]

        assert first_status_item.text(None) == "KICKR: disconnected"

        current = _FakeLoop(connected_dircon=True, attempt_count=2)

        assert first_status_item.text(None) == "KICKR: connected"
    finally:
        icon._stop_status_poller.set()
