"""The background sync loop, started with the API (app/main.py lifespan).

Runs :func:`bizos.sync.runner.sync_all` every ``INTEGRATION_SYNC_INTERVAL_SEC`` seconds (default 300) in a
daemon thread, so slow Composio calls never block requests. ``INTEGRATION_SYNC_ENABLED=off`` turns it off;
people can still press "Sync now" on the Integrations page.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from agno.utils.log import log_info

from bizos import settings

STARTUP_DELAY_SEC = 30

_state: dict[str, Optional[float]] = {"last_finished": None, "started": None}


def seconds_until_next() -> Optional[int]:
    if not settings.integration_sync_enabled() or _state["started"] is None:
        return None
    base = _state["last_finished"] or (_state["started"] + STARTUP_DELAY_SEC - settings.integration_sync_interval())
    return max(0, int(base + settings.integration_sync_interval() - time.time()))


def start(run: Optional[Callable[[], object]] = None) -> Optional[Callable[[], None]]:
    """Start the loop; returns a stop function, or None when sync is turned off."""
    if not settings.integration_sync_enabled():
        log_info("integration sync: off (INTEGRATION_SYNC_ENABLED)")
        return None
    if run is None:
        from bizos.sync.runner import sync_all as run

    stop = threading.Event()
    _state["started"] = time.time()

    def loop() -> None:
        if stop.wait(STARTUP_DELAY_SEC):
            return
        while not stop.is_set():
            try:
                run()
            finally:
                _state["last_finished"] = time.time()
            if stop.wait(settings.integration_sync_interval()):
                return

    thread = threading.Thread(target=loop, name="integration-sync", daemon=True)
    thread.start()
    log_info(f"integration sync: every {settings.integration_sync_interval()}s")
    return stop.set
