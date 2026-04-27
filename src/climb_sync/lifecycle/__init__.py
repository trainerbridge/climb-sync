"""App lifecycle - single-instance lock, logging setup."""
from __future__ import annotations

from .logging_setup import setup_logging
from .single_instance import AlreadyRunning, SingleInstanceLock, acquire_single_instance

__all__ = [
    "SingleInstanceLock",
    "AlreadyRunning",
    "acquire_single_instance",
    "setup_logging",
]
