"""Climb Sync - Windows tray app entry point."""
from __future__ import annotations

import argparse
import ctypes
import logging
import sys

from climb_sync.app import AppShell
from climb_sync.config import is_valid_ipv4, load_config, lock_path
from climb_sync.lifecycle.logging_setup import setup_logging
from climb_sync.lifecycle.single_instance import AlreadyRunning, SingleInstanceLock

logger = logging.getLogger(__name__)


def _show_already_running_message() -> None:
    """MessageBox via Win32; no Tk init on the duplicate-launch path."""
    mb_iconwarning = 0x30
    try:
        ctypes.windll.user32.MessageBoxW(
            0,
            "Climb Sync is already running.\n"
            "Look for the icon in your system tray (next to the clock).",
            "Climb Sync",
            mb_iconwarning,
        )
    except Exception:
        print("Climb Sync is already running.", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(prog="climb-sync")
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging.")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Smoke-test mode: run SyncLoop autonomously and exit.",
    )
    parser.add_argument(
        "--ride-start-delay",
        type=int,
        default=15,
        help="--smoke: countdown before starting (seconds).",
    )
    parser.add_argument(
        "--run-seconds",
        type=int,
        default=60,
        help="--smoke: how long to run (seconds).",
    )
    parser.add_argument(
        "--simulate-outage-at",
        type=float,
        default=None,
        help="--smoke: simulate S4Z outage after N seconds.",
    )
    parser.add_argument("--ip", type=str, default=None, help="KICKR IP (skip mDNS).")
    args = parser.parse_args()

    if args.ip and not is_valid_ipv4(args.ip):
        parser.error("--ip must be an IPv4 address, for example 192.168.26.65")

    config = load_config()
    setup_logging(verbose=args.verbose, level=config.log_level)

    if args.smoke:
        from climb_sync.smoke import run_smoke

        return run_smoke(
            ride_start_delay=args.ride_start_delay,
            run_seconds=args.run_seconds,
            ip=args.ip,
            simulate_outage_at=args.simulate_outage_at,
        )

    lock = SingleInstanceLock(lock_path())
    try:
        lock.acquire()
    except AlreadyRunning:
        _show_already_running_message()
        return 1

    try:
        if args.ip:
            # Dev/operator-only override from a trusted shell; config file input
            # remains validated through load_config().
            config = config.replace(kickr_ip=args.ip)

        from climb_sync.tray.app import build_tray_icon

        app = AppShell(config, tray_icon_factory=build_tray_icon)
        return app.run()
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
