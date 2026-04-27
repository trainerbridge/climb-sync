"""Tk modal dialog for the Override KICKR IP menu item."""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import simpledialog

logger = logging.getLogger(__name__)


def ask_kickr_ip(root: tk.Tk, current: str | None) -> str | None:
    """Prompt for a KICKR IP. Returns a stripped string or None on cancel."""
    initial = current or ""
    result = simpledialog.askstring(
        title="Override KICKR IP",
        prompt="Enter the KICKR IP address (e.g. 192.168.26.65):",
        initialvalue=initial,
        parent=root,
    )
    if result is None:
        logger.info("ip-override: user cancelled")
        return None
    result = result.strip()
    if not result:
        logger.info("ip-override: empty input treated as cancel")
        return None
    return result
