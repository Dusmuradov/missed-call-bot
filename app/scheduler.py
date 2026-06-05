"""
APScheduler: периодические задачи.

Джобы:
  - check_callback_escalations: каждые N минут проверяет пропущенные звонки
    без перезвона и отправляет эскалации в Telegram.
  - refresh_amocrm_token: каждые 6 часов проверяет и обновляет токен AmoCRM.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


async def check_callback_escalations() -> None:
    """
    Находит пропущенные звонки, по которым не перезвонили за N минут,
    и отправляет эскалационные уведомления в Telegram.
    """
    from app.db import get_session
    from app.formatting import build_escalation_message
    from app.repository import get_unescalated_overdue, mark_escalated
    from app.telegram import send_escalation

    check_minutes = settings.callback_check_minutes
    threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=check_minutes)

    logger.debug("Running callback escalation check (threshold=%s)", threshold)

    try:
        async with get_session() as session:
            overdue = await get_unescalated_overdue(session, before_utc=threshold)

        if not overdue:
            return

        logger.info("Found %d overdue missed calls without callback.", len(overdue))

        for tracking in overdue:
            text = build_escalation_message(
                caller=tracking.external_number,
                missed_at=tracking.missed_at_utc,
                operator_name=None,  # оператор хранится в call — не грузим для простоты
                tracking_id=tracking.id,
                timezone_str=settings.timezone,
            )
            msg_id = await send_escalation(text, tracking_id=tracking.id)

            async with get_session() as session:
                # Перезагружаем объект в новой сессии
                from app.repository import get_tracking_by_id
                t = await get_tracking_by_id(session, tracking.id)
                if t:
                    await mark_escalated(session, t, tg_message_id=msg_id)

    except Exception as exc:
        logger.exception("Callback escalation check failed: %s", exc)


async def refresh_amocrm_token_job() -> None:
    """Проактивно обновляет AmoCRM access_token если истекает через <30 мин."""
    if not settings.amocrm_subdomain:
        return

    try:
        from app.amocrm.client import get_valid_client
        client = await get_valid_client()
        if client:
            logger.debug("AmoCRM token OK (subdomain=%s)", client.subdomain)
        else:
            logger.warning("AmoCRM token refresh failed or token not configured.")
    except Exception as exc:
        logger.exception("AmoCRM token refresh job failed: %s", exc)


def create_scheduler() -> AsyncIOScheduler:
    """Создаёт и настраивает планировщик."""
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone="UTC")

    # Проверка перезвонов каждые N минут
    _scheduler.add_job(
        check_callback_escalations,
        trigger="interval",
        minutes=settings.callback_check_minutes,
        id="callback_escalation",
        name="Missed call callback escalation",
        replace_existing=True,
    )

    # Обновление токена AmoCRM каждые 6 часов
    _scheduler.add_job(
        refresh_amocrm_token_job,
        trigger="interval",
        hours=6,
        id="amocrm_token_refresh",
        name="AmoCRM token refresh",
        replace_existing=True,
    )

    return _scheduler


def get_scheduler() -> Optional[AsyncIOScheduler]:
    return _scheduler


def start_scheduler() -> AsyncIOScheduler:
    scheduler = create_scheduler()
    scheduler.start()
    logger.info(
        "Scheduler started. Callback check every %d min.", settings.callback_check_minutes
    )
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped.")
