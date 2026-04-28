"""Grade-source staleness tracking (D-11, D-12).

The SYNC-03 recast: instead of detecting ERG mode (impossible in the
transport-split architecture — we don't talk to Zwift), we detect when the
grade-source WebSocket stops emitting. Thresholds per D-11:
  0-5s     fresh
  5-30s    warn (log once)
  >30s     outage (log once, trigger S4Z reconnect)

Per D-12, outage does NOT stop DIRCON writes — the last smoothed value
continues being written on the 1 Hz tick so the Climb holds its position.
"""
from __future__ import annotations

import time

from .constants import STALE_WARN_SECONDS, STALE_OUTAGE_SECONDS


class StalenessTracker:
    """Tracks time-since-last-grade-update using a monotonic clock.

    Single-event-loop by design — no thread safety needed.
    """

    def __init__(self) -> None:
        self._last_update_ts: float | None = None
        self._warn_pending: bool = True
        self._outage_pending: bool = True

    def mark_received(self) -> None:
        """Call on every grade received from S4Z. Re-arms warn/outage log gates."""
        self._last_update_ts = time.monotonic()
        self._warn_pending = True
        self._outage_pending = True

    def state(self, now: float | None = None) -> str:
        """Returns 'fresh' | 'warn' | 'outage' | 'never'."""
        if self._last_update_ts is None:
            return "never"
        age = (now if now is not None else time.monotonic()) - self._last_update_ts
        if age > STALE_OUTAGE_SECONDS:
            return "outage"
        if age > STALE_WARN_SECONDS:
            return "warn"
        return "fresh"

    def age_seconds(self, now: float | None = None) -> float | None:
        if self._last_update_ts is None:
            return None
        return (now if now is not None else time.monotonic()) - self._last_update_ts

    def take_warn_log(self) -> bool:
        """Returns True at most once per outage episode while state is 'warn'."""
        if self._warn_pending and self.state() == "warn":
            self._warn_pending = False
            return True
        return False

    def take_outage_log(self) -> bool:
        """Returns True at most once per outage episode while state is 'outage'."""
        if self._outage_pending and self.state() == "outage":
            self._outage_pending = False
            return True
        return False
