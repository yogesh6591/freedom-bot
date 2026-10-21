"""Optional in-process autonomous scheduler ticker (default OFF).

Prefer the Docker `scheduler` profile / CLI loop in production-like setups.
This ticker is for single-process local convenience only.
"""

from __future__ import annotations

import asyncio
import logging

from acquisition.config import Settings, get_settings

logger = logging.getLogger(__name__)


async def autonomous_scheduler_loop(settings: Settings | None = None) -> None:
    cfg = settings or get_settings()
    tick_seconds = max(5, int(cfg.autonomous_scheduler_tick_seconds or 60))
    logger.info(
        "in-process autonomous scheduler started (tick=%ss).",
        tick_seconds,
    )
    while True:
        try:
            await asyncio.to_thread(_run_tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("in-process autonomous tick failed")
        await asyncio.sleep(tick_seconds)


def _run_tick() -> None:
    from acquisition.db.session import SessionLocal
    from acquisition.services.autonomous_automation_service import AutonomousAutomationService

    with SessionLocal() as db:
        result = AutonomousAutomationService(db).tick(force=False)
        logger.info("autonomous tick: %s", result)
