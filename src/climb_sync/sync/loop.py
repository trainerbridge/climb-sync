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
import contextlib
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
    LONG_OUTAGE_PARK_SECONDS,
)
from .smoothing import ema_update, clamp_grade
from .staleness import StalenessTracker

logger = logging.getLogger(__name__)

MDNS_DISCOVERY_TIMEOUT_SECONDS: float = 10.0
MDNS_RETRY_BACKOFF: tuple[int, ...] = (2, 5, 10, 15, 30)


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

        self._grade_queue: asyncio.Queue[tuple[float, float]] = asyncio.Queue(maxsize=1)
        self._staleness = StalenessTracker()

        self._raw_grade: float | None = None
        self._smoothed: float | None = None
        self._dircon_client: DirconClient | None = None
        self._connected_dircon: bool = False
        self._connected_s4z: bool = False
        self._attempt_count: int = 0
        # Set after a one-shot 0% write past LONG_OUTAGE_PARK_SECONDS of S4Z
        # silence; cleared on the first post-outage sample so writes resume.
        self._long_outage_parked: bool = False

        self._tg: asyncio.TaskGroup | None = None
        self._stop_event = asyncio.Event()
        self._s4z_reconnect_flag = asyncio.Event()

    # --- public API -------------------------------------------------

    async def start(self) -> None:
        """Run the sync loop. Blocks until stop() is called or an unrecoverable error occurs.

        mDNS discovery runs inside _run_dircon (with retry) so S4Z connection
        progress isn't blocked while waiting for the trainer to come online.
        """
        try:
            async with asyncio.TaskGroup() as tg:
                self._tg = tg
                tg.create_task(self._run_grade_source(), name="s4z-grade-source")
                tg.create_task(self._run_dircon(), name="dircon-client")
        finally:
            self._tg = None

    async def stop(self) -> None:
        """Graceful shutdown: just signal. _run_dircon's finally owns the
        flat-write + close so there is no concurrent writer / closer."""
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

    def _publish_grade(self, ts: float, grade: float) -> None:
        """Publish the latest S4Z grade, dropping any stale pending sample."""
        if self._grade_queue.full():
            try:
                self._grade_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._grade_queue.put_nowait((ts, grade))

    # --- callbacks (passed to grade_source_with_reconnect) ----------

    def _on_s4z_connect(self) -> None:
        self._connected_s4z = True

    def _on_s4z_disconnect(self) -> None:
        self._connected_s4z = False

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
        agen = grade_source_with_reconnect(
            self._s4z_url,
            on_connect=self._on_s4z_connect,
            on_disconnect=self._on_s4z_disconnect,
        ).__aiter__()
        next_grade: asyncio.Task | None = asyncio.create_task(agen.__anext__())
        try:
            while not self._stop_event.is_set():
                done, _pending = await asyncio.wait({next_grade}, timeout=1.0)
                if not done:
                    # No frame in the last second: re-check stop_event without
                    # cancelling the in-flight websocket receive.
                    continue

                try:
                    ts, g = next_grade.result()
                except StopAsyncIteration:
                    next_grade = None
                    break

                # Schedule the next read before processing this sample so the
                # websocket receive overlaps with our staleness/queue work.
                if not self._stop_event.is_set():
                    next_grade = asyncio.create_task(agen.__anext__())

                prev_state = self._staleness.state()
                if prev_state in ("outage", "never"):
                    # Re-seed EMA on actual recovery. Setting before put() guarantees
                    # ordering vs the tick body: both run in the same event loop, the
                    # tick reads _smoothed only between awaits, and we mutate before
                    # awaiting on put().
                    self._smoothed = None
                    # Resume writes if we'd parked the Climb during a long outage.
                    self._long_outage_parked = False
                self._raw_grade = g
                self._publish_grade(ts, g)
        finally:
            if next_grade is not None and not next_grade.done():
                next_grade.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await next_grade
            with contextlib.suppress(Exception):
                await agen.aclose()
            self._connected_s4z = False

    async def _resolve_kickr_ip(self) -> str | None:
        """mDNS discovery with forever-retry until stop_event is set.

        Lives in _run_dircon (not start()) so the S4Z task can run independently
        while we wait for the trainer to come online.
        """
        attempt = 0
        while not self._stop_event.is_set():
            logger.info(
                "mDNS: discovering KICKR (attempt %d, timeout %.0fs)",
                attempt + 1, MDNS_DISCOVERY_TIMEOUT_SECONDS,
            )
            try:
                ip = await discover_kickr(timeout=MDNS_DISCOVERY_TIMEOUT_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("mDNS discovery raised; will retry")
                ip = None
            if ip is not None:
                return ip
            delay = MDNS_RETRY_BACKOFF[min(attempt, len(MDNS_RETRY_BACKOFF) - 1)]
            logger.warning(
                "mDNS: KICKR not found within %.0fs; retrying in %ds. "
                "If this persists, use 'Override KICKR IP…' to set the IP manually.",
                MDNS_DISCOVERY_TIMEOUT_SECONDS, delay,
            )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                return None
            except asyncio.TimeoutError:
                pass
            attempt += 1
        return None

    async def _run_dircon(self) -> None:
        """Discover (if needed), connect to KICKR DIRCON, run 1 Hz tick. Reconnect forever on drop."""
        if self._kickr_ip is None:
            ip = await self._resolve_kickr_ip()
            if ip is None:
                return
            self._kickr_ip = ip

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
                    stop_event=self._stop_event,
                )
            except asyncio.CancelledError:
                raise
            if client is None:
                break

            self._dircon_client = client
            self._connected_dircon = True

            try:
                await self._sync_tick_loop(client)
            except (ConnectionError, OSError, asyncio.TimeoutError) as e:
                logger.warning("DIRCON dropped: %s; will reconnect", e)
            finally:
                self._connected_dircon = False
                # Flat-on-exit only on graceful stop. After connection drop,
                # the writer is already broken and any send would just raise.
                if self._stop_event.is_set():
                    try:
                        await client.set_climb_grade(0.0)
                    except Exception as e:
                        logger.warning("flat-on-exit write failed: %s", e)
                try:
                    await client.close()
                except Exception:
                    pass
                self._dircon_client = None

    async def _sync_tick_loop(self, client: DirconClient) -> None:
        """1 Hz tick body. Runs until stop or DIRCON drop."""
        last_status_log = time.monotonic()
        while not self._stop_event.is_set():
            if client.disconnected():
                raise ConnectionError("DIRCON receive loop ended")

            # 1. Drain queue — latest-wins (D-09 no deadband, always write)
            latest_grade: float | None = None
            while not self._grade_queue.empty():
                try:
                    _ts, g = self._grade_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                latest_grade = g
            if latest_grade is not None:
                self._raw_grade = latest_grade
                self._staleness.mark_received()
                self._smoothed = ema_update(self._smoothed, latest_grade)

            # 2. Log staleness transitions (once per period)
            if self._staleness.take_warn_log():
                logger.warning(
                    "grade.stale: S4Z quiet for >%.0fs", STALE_WARN_SECONDS
                )
            elif self._staleness.take_outage_log():
                logger.error(
                    "grade.outage: S4Z silent for >%.0fs; holding last smoothed value on Climb",
                    STALE_OUTAGE_SECONDS,
                )
                # D-12: hold last value on Climb — do NOT clear _smoothed here.
                # EMA re-seed on actual S4Z recovery is handled in _run_grade_source
                # (Pitfall 4: re-seed on first post-outage sample, not on silence threshold).

            # 3. Write current smoothed — D-12 holds last value through outage,
            # but if S4Z stays silent past LONG_OUTAGE_PARK_SECONDS we park the
            # Climb at 0% with one final write and suppress further writes
            # until S4Z resumes (handled by the recovery branch in
            # _run_grade_source, which clears _long_outage_parked).
            age = self._staleness.age_seconds()
            if age is not None and age >= LONG_OUTAGE_PARK_SECONDS:
                if not self._long_outage_parked:
                    # Re-raise transport errors up to _run_dircon for reconnect;
                    # we set the flag only after the write succeeds so a failed
                    # park-write retries on the next reconnect tick.
                    await client.set_climb_grade(0.0)
                    self._long_outage_parked = True
                    logger.warning(
                        "grade.long_outage_park: S4Z silent for >%.0fs; parked Climb at 0%% and pausing writes until S4Z resumes",
                        LONG_OUTAGE_PARK_SECONDS,
                    )
            elif self._smoothed is not None:
                clamped = clamp_grade(self._smoothed)
                # Re-raise transport errors up to _run_dircon for reconnect.
                await client.set_climb_grade(clamped)

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

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=WRITE_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                pass
