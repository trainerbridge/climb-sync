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
