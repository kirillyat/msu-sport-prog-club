from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta

from app.config import settings
from app.db import SessionLocal
from app.models import utcnow
from app.services.catalog import get_state, sync_catalog
from app.services.sync import relink_orphan_submissions, sync_all

logger = logging.getLogger(__name__)

CATALOG_KEY = "catalog_synced_at"


async def _catalog_is_stale() -> bool:
    async with SessionLocal() as session:
        raw = await get_state(session, CATALOG_KEY)
    if not raw:
        return True
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return True
    return utcnow() - last > timedelta(hours=settings.catalog_refresh_hours)


async def run_once() -> None:
    if await _catalog_is_stale():
        async with SessionLocal() as session:
            counts = await sync_catalog(session)
            logger.info("каталог обновлён: %s", counts)
            fixed = await relink_orphan_submissions(session)
            if fixed:
                logger.info("подвязано посылок к задачам: %s", fixed)

    async with SessionLocal() as session:
        added = await sync_all(session)
        if added:
            logger.info("новых посылок: %s", added)


async def run_scheduler(stop_event: asyncio.Event) -> None:
    logger.info("планировщик запущен, интервал %s с", settings.sync_interval_seconds)
    # Небольшая задержка на старте, чтобы не конкурировать с первыми запросами.
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop_event.wait(), timeout=5)

    while not stop_event.is_set():
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("итерация планировщика упала")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                stop_event.wait(), timeout=settings.sync_interval_seconds
            )
    logger.info("планировщик остановлен")
