"""mDNS discovery for KICKR trainers — AsyncZeroconf port of spike 004."""
from __future__ import annotations

import asyncio
import logging

from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_wahoo-fitness-tnp._tcp.local."


async def discover_kickr(timeout: float = 8.0) -> str | None:
    """Find the first KICKR IP via mDNS. Returns None if not found within timeout."""
    found: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    def on_state_change(zeroconf, service_type, name, state_change):
        # zeroconf >=0.130 calls handlers with KEYWORD args named exactly
        # (zeroconf, service_type, name, state_change). Parameter names must
        # match — using `zc` as the first param raised TypeError on every mDNS
        # response packet during on-bike testing 2026-04-26.
        if state_change is not ServiceStateChange.Added:
            return
        if not found.done():
            asyncio.ensure_future(_resolve(zeroconf, service_type, name))

    async def _resolve(zc, service_type, name):
        info = AsyncServiceInfo(service_type, name)
        await info.async_request(zc, 3000)
        if info and info.addresses and not found.done():
            ip = ".".join(str(b) for b in info.addresses[0])
            logger.info("mDNS discovered KICKR at %s (%s)", ip, name)
            found.set_result(ip)

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
        await browser.async_cancel()
        await aiozc.async_close()
