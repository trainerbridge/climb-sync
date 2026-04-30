from __future__ import annotations

import asyncio
import time

import pytest

from climb_sync.sync.loop import SyncLoop


class _FakeDirconClient:
    def __init__(self, *, disconnected: bool = False) -> None:
        self._disconnected = disconnected
        self.writes: list[float] = []

    def disconnected(self) -> bool:
        return self._disconnected

    async def set_climb_grade(self, grade: float):
        self.writes.append(grade)
        return b""


@pytest.mark.asyncio
async def test_run_dircon_exits_while_reconnect_is_backing_off(monkeypatch):
    loop = SyncLoop(kickr_ip="127.0.0.1")

    async def failing_reconnect(*args, **kwargs):
        stop_event = kwargs["stop_event"]
        await stop_event.wait()
        return None

    monkeypatch.setattr("climb_sync.sync.loop.with_reconnect", failing_reconnect)

    task = asyncio.create_task(loop._run_dircon())
    await asyncio.sleep(0)
    await loop.stop()

    await asyncio.wait_for(task, timeout=1.0)
    assert loop.status["connected_dircon"] is False


@pytest.mark.asyncio
async def test_sync_tick_loop_applies_only_latest_pending_grade(monkeypatch):
    loop = SyncLoop(kickr_ip="127.0.0.1")
    client = _FakeDirconClient()
    monkeypatch.setattr("climb_sync.sync.loop.WRITE_INTERVAL_SECONDS", 0.01)

    loop._mode = "workout"  # gate open
    loop._publish_grade(1.0, 0.01)
    loop._publish_grade(2.0, 0.02)
    loop._publish_grade(3.0, 0.03)

    async def stop_after_first_write(grade: float):
        client.writes.append(grade)
        await loop.stop()
        return b""

    client.set_climb_grade = stop_after_first_write

    await asyncio.wait_for(loop._sync_tick_loop(client), timeout=1.0)

    assert client.writes == [0.03]
    assert loop.status["last_smoothed"] == 0.03


@pytest.mark.asyncio
async def test_sync_tick_loop_raises_when_dircon_recv_loop_has_ended():
    loop = SyncLoop(kickr_ip="127.0.0.1")
    client = _FakeDirconClient(disconnected=True)

    with pytest.raises(ConnectionError):
        await loop._sync_tick_loop(client)


@pytest.mark.asyncio
async def test_sync_tick_loop_parks_climb_at_zero_after_long_outage(monkeypatch):
    """After LONG_OUTAGE_PARK_SECONDS of S4Z silence, write 0% once and pause."""
    loop = SyncLoop(kickr_ip="127.0.0.1")
    client = _FakeDirconClient()
    monkeypatch.setattr("climb_sync.sync.loop.WRITE_INTERVAL_SECONDS", 0.001)

    # Simulate prior held-grade after S4Z dropped well past the park threshold.
    loop._mode = "workout"  # was actively writing before silence
    loop._smoothed = 0.05
    loop._staleness._last_update_ts = time.monotonic() - 999.0

    async def stop_after_first_write(grade: float):
        client.writes.append(grade)
        await loop.stop()
        return b""

    client.set_climb_grade = stop_after_first_write

    await asyncio.wait_for(loop._sync_tick_loop(client), timeout=1.0)

    assert client.writes == [0.0]
    assert loop._long_outage_parked is True


@pytest.mark.asyncio
async def test_sync_tick_loop_skips_writes_while_parked(monkeypatch):
    """Once parked, subsequent ticks must not write to the Climb."""
    loop = SyncLoop(kickr_ip="127.0.0.1")
    client = _FakeDirconClient()
    monkeypatch.setattr("climb_sync.sync.loop.WRITE_INTERVAL_SECONDS", 0.001)

    loop._mode = "workout"
    loop._smoothed = 0.05
    loop._staleness._last_update_ts = time.monotonic() - 999.0
    loop._long_outage_parked = True  # already parked

    # Stop the loop after a few ticks — no writes should happen.
    async def stop_soon():
        await asyncio.sleep(0.02)
        await loop.stop()

    asyncio.create_task(stop_soon())
    await asyncio.wait_for(loop._sync_tick_loop(client), timeout=1.0)

    assert client.writes == []


@pytest.mark.asyncio
async def test_park_state_clears_when_fresh_grade_arrives(monkeypatch):
    """After parking, a fresh grade clears the parked flag and writes resume."""
    loop = SyncLoop(kickr_ip="127.0.0.1")
    client = _FakeDirconClient()
    monkeypatch.setattr("climb_sync.sync.loop.WRITE_INTERVAL_SECONDS", 0.001)

    # State the recovery branch in _run_grade_source would leave us in:
    # parked flag cleared, smoothed reset, fresh sample queued.
    loop._mode = "workout"
    loop._long_outage_parked = False
    loop._smoothed = None
    loop._publish_grade(1.0, 0.03)

    async def stop_after_first_write(grade: float):
        client.writes.append(grade)
        await loop.stop()
        return b""

    client.set_climb_grade = stop_after_first_write

    await asyncio.wait_for(loop._sync_tick_loop(client), timeout=1.0)

    assert client.writes == [0.03]
    assert loop._long_outage_parked is False


# --- ERG-mode detection (workoutZone gating) -------------------------


@pytest.mark.asyncio
async def test_no_writes_in_unknown_mode(monkeypatch):
    """Default mode is 'unknown' until N samples confirm — must not write."""
    loop = SyncLoop(kickr_ip="127.0.0.1")
    client = _FakeDirconClient()
    monkeypatch.setattr("climb_sync.sync.loop.WRITE_INTERVAL_SECONDS", 0.001)

    loop._smoothed = 0.05  # would-be write target
    assert loop._mode == "unknown"

    async def stop_soon():
        await asyncio.sleep(0.02)
        await loop.stop()

    asyncio.create_task(stop_soon())
    await asyncio.wait_for(loop._sync_tick_loop(client), timeout=1.0)

    assert client.writes == []


@pytest.mark.asyncio
async def test_no_writes_in_free_ride_after_park(monkeypatch):
    """In free_ride mode, write 0% exactly once then stay silent."""
    loop = SyncLoop(kickr_ip="127.0.0.1")
    client = _FakeDirconClient()
    monkeypatch.setattr("climb_sync.sync.loop.WRITE_INTERVAL_SECONDS", 0.001)

    loop._mode = "free_ride"
    loop._smoothed = 0.05  # must be ignored — Zwift owns the Climb
    loop._staleness.mark_received()  # not in long outage

    async def stop_soon():
        await asyncio.sleep(0.05)  # let many ticks pass
        await loop.stop()

    asyncio.create_task(stop_soon())
    await asyncio.wait_for(loop._sync_tick_loop(client), timeout=1.0)

    assert client.writes == [0.0]  # exactly one park write
    assert loop._free_ride_parked is True


@pytest.mark.asyncio
async def test_update_mode_debounces_three_consecutive_samples():
    """Mode flips only when N consecutive samples agree — single zone=None
    flicker mid-workout must not flip to free_ride."""
    loop = SyncLoop(kickr_ip="127.0.0.1")

    # Need 3 confirming samples (default WORKOUT_DEBOUNCE_SAMPLES) to flip.
    loop._update_mode(1)
    assert loop._mode == "unknown"
    loop._update_mode(2)
    assert loop._mode == "unknown"
    loop._update_mode(3)
    assert loop._mode == "workout"

    # Single flicker must NOT flip (window has both None and ints).
    loop._update_mode(None)
    assert loop._mode == "workout"
    loop._update_mode(2)
    assert loop._mode == "workout"

    # Three consecutive Nones flip to free_ride.
    loop._update_mode(None)
    loop._update_mode(None)
    assert loop._mode == "workout"
    loop._update_mode(None)
    assert loop._mode == "free_ride"


@pytest.mark.asyncio
async def test_update_mode_resets_state_on_workout_entry():
    """Entering workout mode clears EMA + park flags so writes resume cleanly."""
    loop = SyncLoop(kickr_ip="127.0.0.1")
    loop._smoothed = 0.05
    loop._long_outage_parked = True
    loop._free_ride_parked = True

    for _ in range(3):
        loop._update_mode(2)

    assert loop._mode == "workout"
    assert loop._smoothed is None  # re-seed EMA
    assert loop._long_outage_parked is False
    assert loop._free_ride_parked is False


@pytest.mark.asyncio
async def test_update_mode_clears_park_flag_on_free_ride_entry():
    """Entering free_ride clears the park flag so the tick loop will park once."""
    loop = SyncLoop(kickr_ip="127.0.0.1")
    # Force into workout first.
    for _ in range(3):
        loop._update_mode(1)
    assert loop._mode == "workout"
    loop._free_ride_parked = True  # stale flag from a previous free ride

    for _ in range(3):
        loop._update_mode(None)

    assert loop._mode == "free_ride"
    assert loop._free_ride_parked is False  # tick loop will park once


@pytest.mark.asyncio
async def test_long_outage_park_does_not_fire_in_free_ride(monkeypatch):
    """If S4Z went silent during free_ride, no park write — Zwift never gave
    us the Climb to begin with, and any write would compete with Zwift on
    reconnect."""
    loop = SyncLoop(kickr_ip="127.0.0.1")
    client = _FakeDirconClient()
    monkeypatch.setattr("climb_sync.sync.loop.WRITE_INTERVAL_SECONDS", 0.001)

    loop._mode = "free_ride"
    loop._free_ride_parked = True  # already did our one-shot park
    loop._smoothed = 0.05
    loop._staleness._last_update_ts = time.monotonic() - 999.0

    async def stop_soon():
        await asyncio.sleep(0.02)
        await loop.stop()

    asyncio.create_task(stop_soon())
    await asyncio.wait_for(loop._sync_tick_loop(client), timeout=1.0)

    assert client.writes == []
    assert loop._long_outage_parked is False
