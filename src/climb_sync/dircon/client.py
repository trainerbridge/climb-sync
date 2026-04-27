"""DIRCON asyncio client — transport layer.

Port of .planning/spikes/004-replay-from-bleak/dircon_client.py DirconClient class.
Wire format (encode/decode in .codec) is frozen (D-04). The I/O model is rewritten
from sync+threaded to asyncio-streams per D-04.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from .codec import (
    OP_ENUM, OP_GET_CHARS, OP_READ, OP_WRITE, OP_SUBSCRIBE, OP_NOTIFY,
    OPCODE_NAMES,
    FTMS_CP, WAHOO_CLIMB,
    FTMS_REQUEST_CONTROL, FTMS_RESET, FTMS_SET_TARGET_POWER, FTMS_START,
    encode_frame, decode_header, encode_grade, encode_target_power,
    uuid_bytes,
)

logger = logging.getLogger(__name__)

DIRCON_DEFAULT_PORT = 36866
# D-06 locked: forever-retry curve, capped at 30s
DIRCON_BACKOFF_CURVE: tuple[int, ...] = (1, 2, 5, 10, 15, 30)


class DirconClient:
    def __init__(self, ip: str, port: int = DIRCON_DEFAULT_PORT, *, logger: logging.Logger | None = None):
        self.ip = ip
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._seq = 0
        self._awaiting: dict[int, asyncio.Future] = {}
        self.notifications: asyncio.Queue = asyncio.Queue()
        self._recv_task: asyncio.Task | None = None
        self._closed = False
        self._log = logger or logging.getLogger(__name__)

    async def connect(self) -> None:
        self._log.info("DIRCON connecting to %s:%d", self.ip, self.port)
        self._reader, self._writer = await asyncio.open_connection(self.ip, self.port)
        self._recv_task = asyncio.create_task(self._recv_loop(), name="dircon-recv")

    async def close(self) -> None:
        self._closed = True
        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        # Fail any outstanding awaiters so callers see the error instead of hanging
        for fut in self._awaiting.values():
            if not fut.done():
                fut.set_exception(ConnectionError("DirconClient closed"))
        self._awaiting.clear()

    async def _recv_loop(self) -> None:
        buf = b""
        try:
            while not self._closed:
                chunk = await self._reader.read(4096)
                if not chunk:
                    raise ConnectionResetError("DIRCON peer closed the TCP connection")
                buf += chunk
                while len(buf) >= 6:
                    hdr = decode_header(buf[:6])
                    if hdr is None or hdr[0] != 0x01:
                        # resync: drop one byte (preserved from spike 004 lines 199-203)
                        buf = buf[1:]
                        continue
                    _, opcode, seq, length = hdr
                    if len(buf) < 6 + length:
                        break
                    payload = buf[6:6 + length]
                    buf = buf[6 + length:]
                    if opcode == OP_NOTIFY:
                        await self.notifications.put((time.monotonic(), payload))
                    else:
                        fut = self._awaiting.pop(seq, None)
                        if fut is not None and not fut.done():
                            fut.set_result((opcode, payload))
                        # else: late/duplicate response — drop
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._log.warning("DIRCON recv loop ending: %s", e)
            for fut in self._awaiting.values():
                if not fut.done():
                    fut.set_exception(e)
            self._awaiting.clear()
            # Sentinel so orchestrator can notice and trigger reconnect
            await self.notifications.put(("__disconnected__", e))

    async def _send_and_wait(self, opcode: int, payload: bytes, *, timeout: float = 3.0) -> tuple[int, bytes]:
        seq = (self._seq + 1) & 0xFFFF
        self._seq = seq
        fut = asyncio.get_running_loop().create_future()
        self._awaiting[seq] = fut
        frame = encode_frame(opcode, seq, payload)
        self._writer.write(frame)
        await self._writer.drain()
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._awaiting.pop(seq, None)
            raise

    # --- high-level DIRCON methods (same names as spike 004 class) ---

    async def enumerate_services(self) -> bytes:
        _, payload = await self._send_and_wait(OP_ENUM, b"")
        return payload

    async def get_characteristics(self, service_uuid) -> bytes:
        _, payload = await self._send_and_wait(OP_GET_CHARS, uuid_bytes(service_uuid))
        return payload

    async def read_char(self, char_uuid) -> bytes:
        _, payload = await self._send_and_wait(OP_READ, uuid_bytes(char_uuid))
        return payload

    async def write_char(self, char_uuid, data: bytes) -> bytes:
        _, payload = await self._send_and_wait(OP_WRITE, uuid_bytes(char_uuid) + data)
        return payload

    async def subscribe(self, char_uuid, enable: bool = True) -> bytes:
        enable_byte = b"\x01" if enable else b"\x00"
        _, payload = await self._send_and_wait(OP_SUBSCRIBE, uuid_bytes(char_uuid) + enable_byte)
        return payload

    # --- FTMS convenience (spike-parity only — NOT called by production SyncLoop) ---

    async def ftms_request_control(self) -> bytes:
        return await self.write_char(FTMS_CP, bytes([FTMS_REQUEST_CONTROL]))

    async def ftms_reset(self) -> bytes:
        return await self.write_char(FTMS_CP, bytes([FTMS_RESET]))

    async def ftms_start(self) -> bytes:
        return await self.write_char(FTMS_CP, bytes([FTMS_START]))

    async def ftms_set_target_power(self, watts: int) -> bytes:
        return await self.write_char(FTMS_CP, encode_target_power(watts))

    # --- Wahoo Climb grade (the ONLY thing production SyncLoop calls) ---

    async def set_climb_grade(self, grade_fraction: float) -> bytes:
        """Write grade command to the KICKR Climb via Wahoo opcode 0x66 on a026e037."""
        return await self.write_char(WAHOO_CLIMB, encode_grade(grade_fraction))


async def with_reconnect(
    connect_fn: Callable[[], Awaitable],
    *,
    logger: logging.Logger,
    delays: tuple[int | float, ...] = DIRCON_BACKOFF_CURVE,
):
    """Retry connect_fn forever with the given backoff curve (D-06: never gives up).

    Returns the result of connect_fn on first success.
    Curve is clamped at the last element, so a curve of (1, 2, 5, 10, 15, 30)
    caps at 30s no matter how many attempts.
    """
    attempt = 0
    while True:
        try:
            return await connect_fn()
        except Exception as e:
            delay = delays[min(attempt, len(delays) - 1)]
            logger.warning(
                "connect attempt %d failed (%s); retrying in %ss",
                attempt + 1, e, delay,
            )
            await asyncio.sleep(delay)
            attempt += 1
