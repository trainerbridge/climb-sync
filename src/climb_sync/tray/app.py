"""pystray Icon factory + 1Hz status poller.

D-01: pystray library; D-02: menu structure; D-03: green/yellow/red icon.

Threading: this module's `build_tray_icon()` is invoked from AppShell._pystray_target
which runs in the pystray worker thread (daemon=True). The 1Hz status poller is
a nested daemon thread that lives for the icon's lifetime. It reads
sync_loop.status synchronously and assigns icon.icon / icon.title directly.

Menu callbacks delegate to AppShell. The AppShell methods do their own
thread-marshalling:
- restart_sync     -> asyncio.run_coroutine_threadsafe(...)
- show_override_ip -> root.after(0, ...)
- exit_app         -> root.after(0, root.quit)
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from .icons import generate_icon

logger = logging.getLogger(__name__)


def _icon_color_for(status: dict) -> str:
    """D-03: green / yellow / red color decision from loop.status dict."""
    dircon = status["connected_dircon"]
    s4z = status["connected_s4z"]
    stale = status["staleness"]
    if not dircon and not s4z:
        return "red"
    if stale == "outage":
        return "red"
    if dircon and s4z and stale == "fresh":
        return "green"
    return "yellow"


def _tooltip_for(status: dict) -> str:
    """Short tooltip string built from loop.status (D-03 hover text)."""
    parts = []
    parts.append("KICKR: ok" if status["connected_dircon"] else "KICKR: disconnected")
    parts.append("S4Z: ok" if status["connected_s4z"] else "S4Z: disconnected")
    smoothed = status.get("last_smoothed")
    if smoothed is not None:
        parts.append(f"grade: {smoothed * 100:+.1f}%")
    parts.append(f"state: {status['staleness']}")
    return " | ".join(parts)


def _status_block_lines(status: dict) -> list[str]:
    """D-02 Status block - informational menu lines, not clickable."""
    raw = status.get("last_grade")
    smoothed = status.get("last_smoothed")
    raw_s = f"{raw * 100:+.1f}%" if raw is not None else "-"
    smoothed_s = f"{smoothed * 100:+.1f}%" if smoothed is not None else "-"
    return [
        f"KICKR: {'connected' if status['connected_dircon'] else 'disconnected'}",
        f"S4Z:   {'connected' if status['connected_s4z'] else 'disconnected'}",
        f"Grade (raw):      {raw_s}",
        f"Grade (smoothed): {smoothed_s}",
        f"Staleness: {status['staleness']}",
        f"Reconnect attempts: {status['attempt_count']}",
    ]


def build_tray_icon(
    *,
    sync_loop=None,
    get_sync_loop: Callable[[], Any] | None = None,
    on_restart_sync: Callable[[], None],
    on_override_ip: Callable[[], None],
    on_exit: Callable[[], None],
    poll_interval_seconds: float = 1.0,
) -> Any:
    """Construct the pystray.Icon with the D-02 menu and start the poller."""
    import pystray

    if get_sync_loop is None:
        if sync_loop is None:
            raise ValueError("build_tray_icon requires sync_loop or get_sync_loop")
        get_sync_loop = lambda: sync_loop

    def _current_status() -> dict:
        return get_sync_loop().status

    images = {
        "green": generate_icon("green"),
        "yellow": generate_icon("yellow"),
        "red": generate_icon("red"),
    }

    def _exit_handler(icon) -> None:
        try:
            on_exit()
        finally:
            stop_event.set()
            icon.stop()

    def _build_menu():
        items: list[Any] = []
        for index in range(len(_status_block_lines(_current_status()))):
            items.append(
                pystray.MenuItem(
                    lambda _item, i=index: _status_block_lines(_current_status())[i],
                    None,
                    enabled=False,
                )
            )
        items.append(pystray.MenuItem("Override KICKR IP…", lambda icon, item: on_override_ip()))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Restart sync", lambda icon, item: on_restart_sync()))
        items.append(pystray.MenuItem("Exit", lambda icon, item: _exit_handler(icon)))
        return pystray.Menu(*items)

    initial_status = _current_status()
    initial_color = _icon_color_for(initial_status)
    icon = pystray.Icon(
        name="climb-sync",
        icon=images[initial_color],
        title=_tooltip_for(initial_status),
        menu=pystray.Menu(_build_menu),
    )

    stop_event = threading.Event()
    icon._stop_status_poller = stop_event

    def _poll_status() -> None:
        last_color = initial_color
        last_menu_signature: tuple[str, ...] | None = None
        last_title: str | None = None
        while not stop_event.wait(poll_interval_seconds):
            try:
                status = _current_status()
                color = _icon_color_for(status)
                if color != last_color:
                    icon.icon = images[color]
                    last_color = color
                title = _tooltip_for(status)
                if title != last_title:
                    icon.title = title
                    last_title = title
                # Only re-render the menu when the status block actually
                # changed; saves a per-second pystray round-trip.
                signature = tuple(_status_block_lines(status))
                if signature != last_menu_signature:
                    icon.update_menu()
                    last_menu_signature = signature
            except Exception:
                logger.exception("status poller iteration failed")

    poller_thread = threading.Thread(
        target=_poll_status,
        name="tray-status-poller",
        daemon=True,
    )
    poller_thread.start()

    return icon
