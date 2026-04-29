from __future__ import annotations

import asyncio
import logging

import pytest

from climb_sync.dircon.client import DirconClient, with_reconnect


@pytest.mark.asyncio
async def test_with_reconnect_returns_none_when_stop_event_set_during_backoff():
    stop_event = asyncio.Event()
    attempts = 0

    async def connect():
        nonlocal attempts
        attempts += 1
        raise OSError("offline")

    task = asyncio.create_task(
        with_reconnect(
            connect,
            logger=logging.getLogger(__name__),
            delays=(30,),
            stop_event=stop_event,
        )
    )
    await asyncio.sleep(0)
    stop_event.set()

    assert await asyncio.wait_for(task, timeout=1.0) is None
    assert attempts == 1


@pytest.mark.asyncio
async def test_send_and_wait_without_connection_raises_connection_error():
    client = DirconClient("127.0.0.1")

    with pytest.raises(ConnectionError):
        await client.enumerate_services()
