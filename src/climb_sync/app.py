"""AppShell - three-thread orchestrator for the Windows tray app.

Threading layout:
  - Main thread: Tk root.mainloop() plus lifecycle/exit.
  - Worker thread "asyncio": runs SyncLoop.start() in a fresh event loop.
  - Worker thread "pystray" (daemon=True): runs Icon.run(), supplied in 03-02.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import tkinter as tk
from typing import Any, Callable

from .config import Config, load_config, save_config
from .sync import SyncLoop
from .tray.dialogs import ask_kickr_ip

logger = logging.getLogger(__name__)

TrayIconFactory = Callable[..., Any]


class AppShell:
    """Production tray-app orchestrator."""

    def __init__(
        self,
        config: Config,
        *,
        tray_icon_factory: TrayIconFactory | None = None,
    ) -> None:
        self.config = config
        self.sync_loop: SyncLoop = SyncLoop(
            kickr_ip=config.kickr_ip,
            s4z_url=config.s4z_url,
        )
        self._tray_icon_factory = tray_icon_factory

        self._asyncio_loop: asyncio.AbstractEventLoop | None = None
        self._asyncio_loop_ready = threading.Event()
        self._asyncio_thread: threading.Thread | None = None
        self._sync_start_future: asyncio.Future | None = None

        self._pystray_icon: Any | None = None
        self._pystray_thread: threading.Thread | None = None
        self._pystray_stop_event = threading.Event()

        self._tk_root: tk.Tk | None = None
        self._stopping = threading.Event()

    # --- public API ---------------------------------------------------

    def run(self) -> int:
        """Main thread entry. Blocks until exit. Returns process exit code."""
        self._asyncio_thread = threading.Thread(
            target=self._asyncio_target,
            name="asyncio",
            daemon=False,
        )
        self._asyncio_thread.start()

        # Block until the asyncio thread has installed its event loop, so the
        # tray-thread's restart_sync()/exit_app() bridges always have a loop
        # to call_soon_threadsafe into. 5 seconds is generous; cold launch with
        # AV scanning has been observed at ~1s in field reports.
        if not self._asyncio_loop_ready.wait(timeout=5.0):
            logger.error(
                "asyncio thread did not signal loop-ready within 5s; "
                "tray bridges may no-op until the loop comes up"
            )

        self._pystray_thread = threading.Thread(
            target=self._pystray_target,
            name="pystray",
            daemon=True,
        )
        self._pystray_thread.start()

        self._tk_root = tk.Tk()
        self._tk_root.withdraw()
        try:
            self._tk_root.mainloop()
        finally:
            self._shutdown()
        return 0

    # --- bridge methods ----------------------------------------------

    def restart_sync(self) -> None:
        """Schedule SyncLoop restart on the asyncio thread."""
        if self._asyncio_loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(
            self._restart_sync_async(),
            self._asyncio_loop,
        )
        future.add_done_callback(self._log_restart_result)

    def _log_restart_result(self, future) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("restart_sync failed")

    async def _restart_sync_async(self) -> None:
        """Stop fully before constructing a new SyncLoop."""
        await self.sync_loop.stop()
        if self._sync_start_future is not None:
            try:
                await self._sync_start_future
            except Exception:
                logger.exception("prior sync_loop.start() future raised on stop")
        self.sync_loop = SyncLoop(
            kickr_ip=self.config.kickr_ip,
            s4z_url=self.config.s4z_url,
        )
        self._sync_start_future = asyncio.ensure_future(self.sync_loop.start())

    def show_override_ip_dialog(self) -> None:
        """Bounce the dialog onto the Tk thread."""
        if self._tk_root is None:
            return
        self._tk_root.after(0, self._show_override_ip_on_tk_thread)

    def _show_override_ip_on_tk_thread(self) -> None:
        if self._tk_root is None:
            return
        new_ip = ask_kickr_ip(self._tk_root, current=self.config.kickr_ip)
        if new_ip is None:
            return
        try:
            save_config(self.config.replace(kickr_ip=new_ip))
        except ValueError:
            logger.warning("ip-override: %r rejected as invalid; not applied", new_ip)
            return
        self.config = load_config()
        if self.config.kickr_ip != new_ip:
            logger.warning("ip-override: %r rejected as invalid; not applied", new_ip)
            return
        logger.info("ip-override: applied %r; restarting sync", new_ip)
        self.restart_sync()

    def exit_app(self) -> None:
        """Graceful shutdown signal."""
        self._stopping.set()
        if self._tk_root is not None:
            self._tk_root.after(0, self._tk_root.quit)

    # --- thread targets -----------------------------------------------

    def _asyncio_target(self) -> None:
        """Run a fresh asyncio loop in this thread."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._asyncio_loop = loop
        finally:
            # Always release the main-thread waiter so run() doesn't block
            # forever if loop construction itself failed.
            self._asyncio_loop_ready.set()

        async def driver() -> None:
            self._sync_start_future = asyncio.ensure_future(self.sync_loop.start())
            try:
                await self._sync_start_future
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("SyncLoop.start() crashed")

        try:
            loop.run_until_complete(driver())
        except Exception:
            logger.exception("asyncio thread crashed")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    def _pystray_target(self) -> None:
        """pystray Icon.run() blocks here when 03-02 supplies the factory."""
        if self._tray_icon_factory is None:
            logger.info("pystray stub - 03-02 will replace _tray_icon_factory")
            self._pystray_stop_event.wait()
            return
        try:
            self._pystray_icon = self._tray_icon_factory(
                get_sync_loop=lambda: self.sync_loop,
                on_restart_sync=self.restart_sync,
                on_override_ip=self.show_override_ip_dialog,
                on_exit=self.exit_app,
            )
            self._pystray_icon.run()
        except Exception:
            logger.exception("pystray thread crashed")

    # --- shutdown -----------------------------------------------------

    def _shutdown(self) -> None:
        """Best-effort cleanup; shutdown paths must not raise."""
        stopped_cleanly = False
        if self._asyncio_loop is not None:
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self.sync_loop.stop(),
                    self._asyncio_loop,
                )
                fut.result(timeout=5.0)
                stopped_cleanly = True
            except Exception:
                logger.exception("sync_loop.stop() failed during shutdown")

        if self._asyncio_thread is not None:
            try:
                self._asyncio_thread.join(timeout=5.0)
            except Exception:
                pass
            if self._asyncio_thread.is_alive() and self._asyncio_loop is not None:
                logger.warning("asyncio thread did not stop cleanly; forcing loop stop")
                if self._sync_start_future is not None:
                    try:
                        self._asyncio_loop.call_soon_threadsafe(
                            self._sync_start_future.cancel
                        )
                    except Exception:
                        pass
                try:
                    self._asyncio_loop.call_soon_threadsafe(self._asyncio_loop.stop)
                except Exception:
                    pass
                try:
                    self._asyncio_thread.join(timeout=2.0)
                except Exception:
                    pass
            elif not stopped_cleanly:
                logger.warning("asyncio thread stopped after an unclean stop request")

        self._pystray_stop_event.set()
        if self._pystray_icon is not None:
            try:
                self._pystray_icon.stop()
            except Exception:
                pass

        logger.info("shutdown complete")
