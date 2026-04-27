"""System tray UI - pystray Icon + menu + dialogs."""
from __future__ import annotations

from .app import build_tray_icon
from .dialogs import ask_kickr_ip
from .icons import generate_icon

__all__ = ["ask_kickr_ip", "generate_icon", "build_tray_icon"]
