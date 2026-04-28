"""Logging setup - RotatingFileHandler plus optional StreamHandler when isatty.

D-10: %APPDATA%\\climb-sync\\logs\\app.log, 5MB x 5 files.
"""
from __future__ import annotations

import logging.config
import sys

from ..config import log_dir


def setup_logging(verbose: bool = False, level: str = "INFO") -> None:
    """One-shot dictConfig for the app entry point."""
    log_path = log_dir() / "app.log"
    level = "DEBUG" if verbose else level.upper()

    handlers: dict = {
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(log_path),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "default",
            "level": level,
        }
    }

    stdout = getattr(sys, "stdout", None)
    if stdout is not None and stdout.isatty():
        handlers["console"] = {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "default",
            "level": level,
        }

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                }
            },
            "handlers": handlers,
            "root": {"level": level, "handlers": list(handlers.keys())},
        }
    )
