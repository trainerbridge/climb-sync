"""mDNS discovery for KICKR trainers — AsyncZeroconf port of spike 004."""
from __future__ import annotations

import asyncio
import logging
import socket

from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_wahoo-fitness-tnp._tcp.local."


async def discover_kickr(timeout: float = 8.0) -> str | None:
    """Find the first KICKR IPv4 via mDNS. Returns None if not found within timeout."""
    found: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    resolve_tasks: set[asyncio.Task] = set()

    def on_state_change(zeroconf, service_type, name, state_change):
        # zeroconf >=0.130 calls handlers with KEYWORD args named exactly
        # (zeroconf, service_type, name, state_change). Parameter names must
        # match — using `zc` as the first param raised TypeError on every mDNS
        # response packet during on-bike testing 2026-04-26.
        if state_change is not ServiceStateChange.Added:
            return
        if found.done():
            return
        # Hold a strong ref so the task can't be GC'd mid-flight (3.13+ warning).
        task = asyncio.ensure_future(_resolve(zeroconf, service_type, name))
        resolve_tasks.add(task)
        task.add_done_callback(resolve_tasks.discard)

    async def _resolve(zc, service_type, name):
        info = AsyncServiceInfo(service_type, name)
        await info.async_request(zc, 3000)
        if not info or not info.addresses or found.done():
            return
        # Prefer IPv4 (4 bytes); skip IPv6 entries since the DIRCON port we
        # use is observed only on IPv4 in the field.
        for addr in info.addresses:
            if len(addr) == 4:
                ip = socket.inet_ntoa(addr)
                logger.info("mDNS discovered KICKR at %s (%s)", ip, name)
                if not found.done():
                    found.set_result(ip)
                return

    aiozc = AsyncZeroconf()
    browser = AsyncServiceBrowser(
        aiozc.zeroconf,
        SERVICE_TYPE,
        handlers=[on_state_change],
    )
    try:
        return await asyncio.wait_for(found, timeout=timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        # Cancel any in-flight resolves.
        for task in resolve_tasks:
            if not task.done():
                task.cancel()
        if resolve_tasks:
            await asyncio.gather(*resolve_tasks, return_exceptions=True)
        # Shield socket teardown from outer cancellation so zeroconf and the
        # browser don't leak file descriptors when the caller is cancelled
        # mid-discovery.
        cleanup = asyncio.create_task(_close_zeroconf(browser, aiozc))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            # Outer task was cancelled; let cleanup run to completion in
            # background and re-raise.
            raise


async def _close_zeroconf(
    browser: AsyncServiceBrowser, aiozc: AsyncZeroconf
) -> None:
    try:
        await browser.async_cancel()
    except Exception:
        logger.debug("zeroconf browser close failed", exc_info=True)
    try:
        await aiozc.async_close()
    except Exception:
        logger.debug("zeroconf close failed", exc_info=True)
