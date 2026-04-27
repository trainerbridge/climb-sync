"""Sauce4Zwift WebSocket grade reader.

Subscribes to the `athlete/watching` event stream on S4Z's local WebSocket
endpoint (ws://localhost:1080/api/ws/events) and yields live road-grade
fractions. Fixes Phase 1 Gap 2: the spike's subscribe payload was missing
the outer `type: request` wrapper and the `data.method`/`data.arg` nesting.
The payload shape below is verified against SauceLLC/sauce4zwift main @
2026-04-24 (src/webserver.mjs lines 33-52 + 127).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import AsyncIterator, Callable

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = logging.getLogger(__name__)

S4Z_URL = "ws://localhost:1080/api/ws/events"

# Reconnect backoff curve — paralleled from DIRCON D-06. Never gives up.
S4Z_BACKOFF_CURVE: tuple[int, ...] = (1, 2, 5, 10, 15, 30)


async def grade_source(
    url: str = S4Z_URL,
    *,
    on_connect: Callable[[], None] | None = None,
) -> AsyncIterator[tuple[float, float]]:
    """Async generator yielding (monotonic_ts, grade_fraction) tuples.

    The subscribe payload matches SauceLLC's verified format exactly:
        {"type": "request", "uid": <str>,
         "data": {"method": "subscribe",
                  "arg": {"event": "athlete/watching", "subId": <str>}}}

    Yields (time.monotonic(), float(grade)) for every athlete/watching event
    carrying a state.grade field. Skips messages without grade (Pitfall 2).
    Raises RuntimeError on subscribe-failure. ConnectionClosed / OSError
    propagate to the caller — use grade_source_with_reconnect() for forever-retry.
    """
    request_id = f"zw-alt-req-{random.randint(1, 10**8)}"
    sub_id = f"zw-alt-sub-{random.randint(1, 10**8)}"

    async with websockets.connect(url) as ws:
        # EXACT verified payload — do not edit this structure.
        await ws.send(json.dumps({
            "type": "request",
            "uid": request_id,
            "data": {
                "method": "subscribe",
                "arg": {
                    "event": "athlete/watching",
                    "subId": sub_id,
                },
            },
        }))

        # First response is subscribe ack (or failure).
        resp = json.loads(await ws.recv())
        if resp.get("type") == "response" and not resp.get("success", False):
            raise RuntimeError(f"S4Z subscribe failed: {resp.get('error')}")

        logger.info("S4Z subscribe ok; subId=%s", sub_id)
        if on_connect is not None:
            try:
                on_connect()
            except Exception:
                logger.exception("on_connect callback raised")

        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue  # malformed frame — skip (DoS-resilience)

            if msg.get("type") != "event" or not msg.get("success"):
                continue
            state = (msg.get("data") or {}).get("state") or {}
            grade = state.get("grade")
            if grade is None:
                continue  # defensive: no grade field (Pitfall 2)
            try:
                yield (time.monotonic(), float(grade))
            except (TypeError, ValueError):
                continue  # malformed grade value


async def grade_source_with_reconnect(
    url: str = S4Z_URL,
    *,
    delays: tuple[int, ...] = S4Z_BACKOFF_CURVE,
    on_connect: Callable[[], None] | None = None,
    on_disconnect: Callable[[], None] | None = None,
) -> AsyncIterator[tuple[float, float]]:
    """Forever-retry wrapper around grade_source().

    On expected network errors (ConnectionClosed, ConnectionRefusedError,
    OSError, WebSocketException, asyncio.TimeoutError), logs, sleeps per
    the backoff curve, and reconnects with a FRESH async-with-websockets.connect()
    each attempt (Pitfall 6 -- do not store ws outside the context manager).

    Unexpected exceptions are logged and retried rather than escaping into the
    caller's TaskGroup. RuntimeError keeps the existing subscribe-rejected path.
    """
    attempt = 0
    while True:
        try:
            async for ts_grade in grade_source(url, on_connect=on_connect):
                yield ts_grade
            # Generator exited cleanly — S4Z closed the stream.
            attempt = 0
            logger.warning("S4Z stream ended cleanly; reconnecting")
            if on_disconnect is not None:
                try:
                    on_disconnect()
                except Exception:
                    logger.exception("on_disconnect callback raised")
        except (
            ConnectionClosed,
            ConnectionRefusedError,
            OSError,
            WebSocketException,
            asyncio.TimeoutError,
        ) as e:
            if on_disconnect is not None:
                try:
                    on_disconnect()
                except Exception:
                    logger.exception("on_disconnect callback raised")
            delay = delays[min(attempt, len(delays) - 1)]
            logger.warning(
                "S4Z connect attempt %d failed (%s: %s); retrying in %ds",
                attempt + 1, type(e).__name__, e, delay,
            )
            await asyncio.sleep(delay)
            attempt += 1
        except RuntimeError as e:
            if on_disconnect is not None:
                try:
                    on_disconnect()
                except Exception:
                    logger.exception("on_disconnect callback raised")
            logger.error("S4Z subscribe rejected: %s", e)
            await asyncio.sleep(delays[-1])
            attempt += 1
        except Exception as e:
            if on_disconnect is not None:
                try:
                    on_disconnect()
                except Exception:
                    logger.exception("on_disconnect callback raised")
            delay = delays[min(attempt, len(delays) - 1)]
            logger.exception(
                "S4Z unexpected error (%s); retrying in %ds",
                type(e).__name__, delay,
            )
            await asyncio.sleep(delay)
            attempt += 1
