"""Autonomous on-bike smoke test for the production SyncLoop.

Extracted from scripts/smoke_full_sync.py so the packaged executable can run
the same hardware validation through ``climb-sync.exe --smoke``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from .grade.source import grade_source_with_reconnect as _real_grade_source
from .sync import SyncLoop
from .sync import loop as _sync_loop_module

logger = logging.getLogger(__name__)


def _make_silent_after_source(
    real_factory: Callable[..., AsyncIterator[tuple[float, float, int | None]]],
    silent_after_seconds: float,
) -> Callable[..., AsyncIterator[tuple[float, float, int | None]]]:
    """Wrap grade_source_with_reconnect so it goes silent after N seconds."""

    async def silent_after(*args: Any, **kwargs: Any) -> AsyncIterator[tuple[float, float, int | None]]:
        deadline: float | None = None
        async for ts, g, z in real_factory(*args, **kwargs):
            if deadline is None:
                deadline = time.monotonic() + silent_after_seconds
                logger.info(
                    "outage simulator armed: real samples will stop in %.0fs",
                    silent_after_seconds,
                )
            if time.monotonic() >= deadline:
                logger.warning(
                    "outage simulator engaged: ceasing yield (S4Z silence simulated)"
                )
                forever = asyncio.Event()
                await forever.wait()
            yield ts, g, z

    return silent_after


async def _smoke_main(
    ride_start_delay: int,
    run_seconds: int,
    ip: str | None,
    simulate_outage_at: float | None,
) -> int:
    original_source = _sync_loop_module.grade_source_with_reconnect
    patched = simulate_outage_at is not None
    if patched:
        _sync_loop_module.grade_source_with_reconnect = _make_silent_after_source(
            original_source,
            simulate_outage_at,
        )

    print("\n=== SyncLoop smoke test ===")
    print(f"Target: KICKR @ {ip or 'mDNS discovery'} + S4Z @ ws://localhost:1080")
    print(f"Run duration: {run_seconds}s")
    if simulate_outage_at is not None:
        print(
            f"Outage simulator: real samples for ~{simulate_outage_at:.0f}s, "
            "then silent for the rest of the run (D-12 hardware verification)"
        )
    print(f"Countdown: {ride_start_delay}s - mount the bike now.\n")

    for i in range(ride_start_delay, 0, -1):
        print(f"  starting in {i}s ...")
        await asyncio.sleep(1)

    loop = SyncLoop(kickr_ip=ip)
    observed: dict[str, Any] = {
        "dircon_seen_connected": False,
        "s4z_seen_connected": False,
        "max_smoothed": None,
        "fresh_seen": False,
        "outage_seen": False,
        "smoothed_at_first_outage": None,
        "smoothed_held_during_outage": True,
    }

    def observe_status(s: dict[str, Any]) -> None:
        if s["connected_dircon"]:
            observed["dircon_seen_connected"] = True
        if s["connected_s4z"]:
            observed["s4z_seen_connected"] = True
        if s["last_smoothed"] is not None:
            observed["max_smoothed"] = s["last_smoothed"]
        if s["staleness"] == "fresh":
            observed["fresh_seen"] = True
        if s["staleness"] == "outage":
            if not observed["outage_seen"]:
                observed["outage_seen"] = True
                observed["smoothed_at_first_outage"] = s["last_smoothed"]
            elif observed["smoothed_at_first_outage"] is not None and (
                s["last_smoothed"] is None
                or abs(s["last_smoothed"] - observed["smoothed_at_first_outage"]) > 1e-9
            ):
                observed["smoothed_held_during_outage"] = False

    def print_status(elapsed: float, s: dict[str, Any]) -> None:
        grade_s = "n/a" if s["last_grade"] is None else f"{s['last_grade']*100:+.2f}%"
        smoothed_s = (
            "n/a" if s["last_smoothed"] is None else f"{s['last_smoothed']*100:+.2f}%"
        )
        print(
            f"  t={elapsed:5.1f}s  dircon={s['connected_dircon']}  "
            f"s4z={s['connected_s4z']}  last_grade={grade_s}  "
            f"smoothed={smoothed_s}  stale={s['staleness']}"
        )

    start_task = asyncio.create_task(loop.start())
    try:
        start = time.monotonic()
        deadline = start + run_seconds
        s = loop.status
        observe_status(s)
        print_status(0.0, s)
        while time.monotonic() < deadline and not start_task.done():
            s = loop.status
            elapsed = time.monotonic() - start
            observe_status(s)
            print_status(elapsed, s)
            await asyncio.sleep(min(5.0, max(0.0, deadline - time.monotonic())))
    except Exception as e:
        print(f"\nRUN FAILED: {e!r}")
    finally:
        await loop.stop()
        try:
            await asyncio.wait_for(start_task, timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            start_task.cancel()
        if patched:
            _sync_loop_module.grade_source_with_reconnect = original_source

    print("\n=== Summary ===")
    final = loop.status
    print(
        "connected_dircon : "
        f"{final['connected_dircon']} "
        f"(observed_during_run={observed['dircon_seen_connected']})"
    )
    print(
        "connected_s4z    : "
        f"{final['connected_s4z']} "
        f"(observed_during_run={observed['s4z_seen_connected']})"
    )
    print(f"last_grade       : {final['last_grade']}")
    print(f"last_smoothed    : {final['last_smoothed']}")
    print(f"staleness        : {final['staleness']}")
    print(f"attempt_count    : {final['attempt_count']}")

    pass_dircon = observed["dircon_seen_connected"]
    pass_s4z = observed["s4z_seen_connected"] and observed["max_smoothed"] is not None
    if pass_dircon and pass_s4z:
        print("\nRESULT: PASS - SyncLoop ran end-to-end (S4Z -> EMA -> DIRCON)")
    elif pass_s4z:
        print("\nRESULT: PARTIAL - S4Z worked but DIRCON path did not confirm")
    else:
        print("\nRESULT: FAIL - no grade observed; inspect logs")

    pass_outage = True
    if simulate_outage_at is not None:
        print("\n=== D-12 outage-hold verdict ===")
        print(f"outage_seen                    : {observed['outage_seen']}")
        print(f"smoothed_at_first_outage       : {observed['smoothed_at_first_outage']}")
        print(f"smoothed_held_during_outage    : {observed['smoothed_held_during_outage']}")
        pass_outage = (
            observed["outage_seen"]
            and observed["smoothed_at_first_outage"] is not None
            and observed["smoothed_held_during_outage"]
        )
        if pass_outage:
            print(
                "\nOUTAGE-HOLD: PASS - staleness reached 'outage' and the "
                "smoothed value held across all outage status lines"
            )
        elif not observed["outage_seen"]:
            print("\nOUTAGE-HOLD: INCONCLUSIVE - staleness never reached 'outage'")
        else:
            print("\nOUTAGE-HOLD: FAIL - smoothed value changed during outage")

    return 0 if pass_dircon and pass_s4z and pass_outage else 1


def run_smoke(
    *,
    ride_start_delay: int,
    run_seconds: int,
    ip: str | None,
    simulate_outage_at: float | None,
) -> int:
    """Run the autonomous-on-bike smoke test. Returns a process exit code."""
    return asyncio.run(
        _smoke_main(ride_start_delay, run_seconds, ip, simulate_outage_at)
    )
