"""SyncLoop — the grade pipeline orchestrator.

Composes:
  * grade_source_with_reconnect (plan 02-01)   — live grade from S4Z WebSocket
  * DirconClient + with_reconnect (plan 02-02) — DIRCON TCP transport to the KICKR
  * EMA smoothing + clamp                       — visually smooth, hardware-safe writes
  * StalenessTracker                            — warn/outage semantics for S4Z silence

**Transport-split (CLAUDE.md FINAL, D-04):** this loop writes ONLY the Climb
grade channel (Wahoo opcode 0x66 on a026e037). It does NOT call any FTMS
control endpoint — Zwift owns FTMS ERG over BLE. Breaking this rule
collapses the transport-split architecture.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..dircon import DirconClient, WAHOO_CLIMB, discover_kickr, with_reconnect
from ..grade import S4Z_URL, grade_source_with_reconnect
from .constants import (
    WRITE_INTERVAL_SECONDS,
    DIRCON_BACKOFF_CURVE,
    STALE_WARN_SECONDS,
    STALE_OUTAGE_SECONDS,
)
from .smoothing import ema_update, clamp_grade
from .staleness import StalenessTracker

logger = logging.getLogger(__name__)


class SyncLoop:
    """Production grade-sync orchestrator.

    Usage:
        loop = SyncLoop(kickr_ip="192.168.26.65")
        await loop.start()   # runs until cancelled
        # ... app running ...
        await loop.stop()    # graceful: sends 0% grade, closes DIRCON
    """

    def __init__(self, *, kickr_ip: str | None = None, s4z_url: str = S4Z_URL) -> None:
        self._kickr_ip = kickr_ip
        self._s4z_url = s4z_url

        self._grade_queue: asyncio.Queue[tuple[float, float]] = asyncio.Queue()
        self._staleness = StalenessTracker()

        self._raw_grade: float | None = None
        self._smoothed: float | None = None
        self._dircon_client: DirconClient | None = None
        self._connected_dircon: bool = False
        self._connected_s4z: bool = False
        self._attempt_count: int = 0

        self._tg: asyncio.TaskGroup | None = None
        self._stop_event = asyncio.Event()
        self._s4z_reconnect_flag = asyncio.Event()

    # --- public API -------------------------------------------------

    async def start(self) -> None:
        """Run the sync loop. Blocks until stop() is called or an unrecoverable error occurs."""
        if self._kickr_ip is None:
            logger.info("no kickr_ip provided — discovering via mDNS")
            self._kickr_ip = await discover_kickr(timeout=10.0)
            if self._kickr_ip is None:
                raise RuntimeError("KICKR not found via mDNS; pass kickr_ip explicitly")

        try:
            async with asyncio.TaskGroup() as tg:
                self._tg = tg
                tg.create_task(self._run_grade_source(), name="s4z-grade-source")
                tg.create_task(self._run_dircon(), name="dircon-client")
        finally:
            self._tg = None

    async def stop(self) -> None:
        """Graceful shutdown: return Climb to 0% flat, close DIRCON, then signal stop."""
        # Flat-on-exit pattern (spike 005 lines 269-274)
        if self._dircon_client is not None and self._connected_dircon:
            try:
                await self._dircon_client.set_climb_grade(0.0)
            except Exception as e:
                logger.warning("flat-on-exit write failed: %s", e)
            try:
                await self._dircon_client.close()
            except Exception:
                pass
        self._stop_event.set()

    @property
    def status(self) -> dict[str, Any]:
        """Stable Phase 3 seam — polled by the tray app tooltip loop."""
        return {
            "connected_dircon": self._connected_dircon,
            "connected_s4z": self._connected_s4z,
            "last_grade": self._raw_grade,
            "last_smoothed": self._smoothed,
            "staleness": self._staleness.state(),
            "attempt_count": self._attempt_count,
        }

    def mark_s4z_reconnect(self) -> None:
        """Pitfall 4 reset: on S4Z reconnect, re-seed the EMA from the next sample.

        Clears the smoothed state so the next ema_update(None, sample) returns
        sample unchanged (warmup). Without this, a long S4Z outage followed by
        resumption would have the EMA slowly converge from a stale pre-outage value.
        """
        self._smoothed = None

    # --- internal tasks ---------------------------------------------

    async def _run_grade_source(self) -> None:
        """Read grades from S4Z and enqueue them. Forever (until _stop_event set).

        Pitfall 4 — re-seed the EMA on the FIRST sample after outage/never (i.e.
        on actual S4Z recovery, not on the staleness threshold). The previous
        smoothed value is kept across outage so D-12 (HOLD last value during
        outage) holds. Once a fresh sample lands here, we clear `_smoothed`
        so the next ema_update() returns the sample unchanged (warmup-seed)
        instead of slowly converging from a stale pre-outage value.

        D-06 (WR-01) fix: outer stop-event guard so stop() returns cleanly
        even if the underlying source hangs forever waiting for a frame.
        Mirrors the same guard in _run_dircon and _sync_tick_loop.
        """
        try:
            self._connected_s4z = False
            agen = grade_source_with_reconnect(
                self._s4z_url,
                on_connect=lambda: setattr(self, "_connected_s4z", True),
                on_disconnect=lambda: setattr(self, "_connected_s4z", False),
            ).__aiter__()
            next_grade = asyncio.create_task(agen.__anext__())
            while not self._stop_event.is_set():
                done, _pending = await asyncio.wait({next_grade}, timeout=1.0)
                if not done:
                    # No frame in the last second: re-check stop_event without
                    # cancelling the in-flight websocket receive.
                    continue

                try:
                    ts, g = next_grade.result()
                except StopAsyncIteration:
                    break
                finally:
                    if not self._stop_event.is_set():
                        next_grade = asyncio.create_task(agen.__anext__())

                prev_state = self._staleness.state()
                if prev_state in ("outage", "never"):
                    # Re-seed EMA on actual recovery. Setting before put() guarantees
                    # ordering vs the tick body: both run in the same event loop, the
                    # tick reads _smoothed only between awaits, and we mutate before
                    # awaiting on put().
                    self._smoothed = None
                self._connected_s4z = True
                self._raw_grade = g
                await self._grade_queue.put((ts, g))
            if not next_grade.done():
                next_grade.cancel()
        finally:
            self._connected_s4z = False

    async def _run_dircon(self) -> None:
        """Connect to KICKR DIRCON, hold, run 1 Hz tick. Reconnect forever on drop."""

        async def connect_and_hold() -> DirconClient:
            client = DirconClient(self._kickr_ip, logger=logger)
            await client.connect()
            await client.enumerate_services()
            await client.subscribe(WAHOO_CLIMB, enable=True)
            logger.info("DIRCON connected and subscribed to WAHOO_CLIMB at %s", self._kickr_ip)
            return client

        while not self._stop_event.is_set():
            self._attempt_count += 1
            self._connected_dircon = False
            try:
                client = await with_reconnect(
                    connect_and_hold,
                    logger=logger,
                    delays=DIRCON_BACKOFF_CURVE,
                )
            except asyncio.CancelledError:
                raise

            self._dircon_client = client
            self._connected_dircon = True

            try:
                await self._sync_tick_loop(client)
            except (ConnectionError, OSError, asyncio.TimeoutError) as e:
                logger.warning("DIRCON dropped: %s; will reconnect", e)
            finally:
                self._connected_dircon = False
                try:
                    await client.close()
                except Exception:
                    pass
                self._dircon_client = None

    async def _sync_tick_loop(self, client: DirconClient) -> None:
        """1 Hz tick body. Runs until stop or DIRCON drop."""
        last_status_log = time.monotonic()
        while not self._stop_event.is_set():
            # 1. Drain queue — latest-wins (D-09 no deadband, always write)
            drained = False
            while not self._grade_queue.empty():
                try:
                    _ts, g = self._grade_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._raw_grade = g
                self._staleness.mark_received()
                self._smoothed = ema_update(self._smoothed, g)
                drained = True

            # 2. Log staleness transitions (once per period)
            state = self._staleness.state()
            if state == "warn" and not self._staleness._logged_warn:
                logger.warning(
                    "grade.stale: S4Z quiet for >%.0fs", STALE_WARN_SECONDS
                )
                self._staleness._logged_warn = True
            elif state == "outage" and not self._staleness._logged_outage:
                logger.error(
                    "grade.outage: S4Z silent for >%.0fs; holding last smoothed value on Climb",
                    STALE_OUTAGE_SECONDS,
                )
                self._staleness._logged_outage = True
                # D-12: hold last value on Climb — do NOT clear _smoothed here.
                # EMA re-seed on actual S4Z recovery is handled in _run_grade_source
                # (Pitfall 4: re-seed on first post-outage sample, not on silence threshold).

            # 3. Write current smoothed — D-12: hold last value indefinitely
            if self._smoothed is not None:
                clamped = clamp_grade(self._smoothed)
                try:
                    await client.set_climb_grade(clamped)
                except (ConnectionError, OSError, asyncio.TimeoutError):
                    # Re-raise up to _run_dircon which will reconnect
                    raise

            # 4. Optional periodic status line (every 30s like spike 005)
            now = time.monotonic()
            if now - last_status_log >= 30.0:
                logger.info(
                    "status: smoothed=%s raw=%s stale=%s queue=%d",
                    f"{self._smoothed*100:+.2f}%" if self._smoothed is not None else "n/a",
                    f"{self._raw_grade*100:+.2f}%" if self._raw_grade is not None else "n/a",
                    self._staleness.state(),
                    self._grade_queue.qsize(),
                )
                last_status_log = now

            await asyncio.sleep(WRITE_INTERVAL_SECONDS)
