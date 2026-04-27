"""Single-instance lockfile via msvcrt.locking.

D-09: lockfile at %APPDATA%\\climb-sync\\app.lock. The file handle must
persist for the process lifetime, so SingleInstanceLock stores it on self.
"""
from __future__ import annotations

import logging
import msvcrt
import os
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)


class AlreadyRunning(RuntimeError):
    """Raised when SingleInstanceLock.acquire() finds an existing lock holder."""


class SingleInstanceLock:
    """Hold this for the process lifetime; the OS releases it on process death."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._fh: BinaryIO | None = None

    def acquire(self) -> None:
        """Acquire exclusive non-blocking lock. Raises AlreadyRunning on conflict."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.lock_path, "a+b")
        try:
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as e:
            self._fh.close()
            self._fh = None
            raise AlreadyRunning(f"another instance has the lock: {e}") from e

        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(f"{os.getpid()}\n".encode())
        self._fh.flush()
        logger.info(
            "single-instance: acquired lock at %s (pid=%d)",
            self.lock_path,
            os.getpid(),
        )

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        try:
            self._fh.close()
        finally:
            self._fh = None
        logger.info("single-instance: released lock at %s", self.lock_path)


def acquire_single_instance(appdata: Path) -> SingleInstanceLock:
    """Build and acquire a SingleInstanceLock under appdata."""
    lock = SingleInstanceLock(appdata / "app.lock")
    lock.acquire()
    return lock
