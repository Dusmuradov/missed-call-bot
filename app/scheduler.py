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
from apscheduler.triggers.cron import CronTrigger

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
    """Проактивно обновляет AmoCRM access_token если истекает через <2 часа.
    Пропускается если задан AMOCRM_LONG_LIVED_TOKEN."""
    if not settings.amocrm_subdomain or settings.amocrm_long_lived_token:
        return

    try:
        from app.amocrm.client import refresh_access_token
        from app.db import get_session
        from app.repository import get_amocrm_token, upsert_amocrm_token

        async with get_session() as session:
            token_row = await get_amocrm_token(session)

        if token_row is None:
            logger.warning("AmoCRM token not configured, skipping proactive refresh.")
            return

        # Проактивное обновление за 2 часа до истечения (перекрывает 30-минутный интервал)
        needs_refresh = token_row.expires_at is None
        if not needs_refresh:
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            needs_refresh = token_row.expires_at - now_utc < timedelta(hours=2)

        if not needs_refresh:
            logger.debug("AmoCRM token OK, expires at %s", token_row.expires_at)
            return

        logger.info("AmoCRM token expiring within 2h, proactively refreshing…")
        tokens = await refresh_access_token(token_row.refresh_token)
        expires_in = tokens.get("expires_in", 86400)
        new_expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=expires_in)

        async with get_session() as session:
            await upsert_amocrm_token(
                session,
                subdomain=token_row.subdomain,
                access_token=tokens["access_token"],
                refresh_token=tokens["refresh_token"],
                expires_at=new_expires,
            )
        logger.info("AmoCRM token proactively refreshed, expires at %s", new_expires)

    except Exception as exc:
        logger.exception("AmoCRM token refresh job failed: %s", exc)
        try:
            from app.telegram import send_to_user
            if settings.admin_user_id:
                msg = (
                    "⚠️ <b>AmoCRM: плановое обновление токена провалилось!</b>\n"
                    f"Ошибка: <code>{exc}</code>\n\n"
                    "Пройдите повторную авторизацию: /amocrm/oauth/start"
                )
                await send_to_user(settings.admin_user_id, msg)
        except Exception:
            pass


async def daily_report_job() -> None:
    """Ежедневный отчёт за вчерашний день — отправляется в группу в 09:00 по Ташкенту."""
    from app.analytics_utel import get_period_stats
    from app.amocrm.reports import get_lead_metrics_by_users
    from app.db import get_session
    from app.formatting import format_amocrm_users_report, format_daily_utel_report
    from app.periods import period_yesterday
    from app.telegram import send_notification

    logger.info("Running daily report job…")
    from_utc, to_utc = period_yesterday()
    tz = settings.timezone

    # --- Utel отчёт ---
    try:
        async with get_session() as session:
            utel_stats = await get_period_stats(session, from_utc, to_utc, tz_name=tz)
        utel_text = format_daily_utel_report(utel_stats, timezone_str=tz)
    except Exception as exc:
        logger.error("Daily report: Utel stats failed: %s", exc)
        utel_text = "📞 <b>Звонки Utel — Вчера</b>\n⚠️ Ошибка получения данных"

    # --- AmoCRM отчёт ---
    try:
        amo_result = await get_lead_metrics_by_users(from_utc, to_utc, tz_name=tz)
        if amo_result.get("error"):
            amo_text = f"📋 <b>AmoCRM лиды — Вчера</b>\n⚠️ {amo_result['error']}"
        else:
            amo_text = format_amocrm_users_report(
                amo_result, "Вчера", from_utc=from_utc, to_utc=to_utc, timezone_str=tz
            )
    except Exception as exc:
        logger.error("Daily report: AmoCRM stats failed: %s", exc)
        amo_text = "📋 <b>AmoCRM лиды — Вчера</b>\n⚠️ Ошибка получения данных"

    # Отправляем двумя сообщениями чтобы не превышать лимит 4096 символов
    await send_notification(utel_text)
    await send_notification(amo_text)
    logger.info("Daily report sent.")


async def hermes_morning_digest_job() -> None:
    """Отправляет персональный дайджест каждому менеджеру с amocrm_user_id."""
    if not settings.deepseek_api_key:
        logger.debug("Hermes digest skipped: DEEPSEEK_API_KEY not set.")
        return

    try:
        from app.db import get_session
        from app.repository import list_bot_users
        from app.telegram import send_to_user
        from hermes.audit import run_audit
        from hermes.digest import format_digest

        async with get_session() as session:
            all_users = await list_bot_users(session)

        targets = [u for u in all_users if u.amocrm_user_id and u.role in ("seller", "manager")]
        logger.info("Hermes digest: sending to %d users", len(targets))

        for user in targets:
            try:
                deals = await run_audit(user.amocrm_user_id, user.tg_user_id, with_suggestions=False)
                if not deals:
                    continue
                name = user.full_name or user.username or "Менеджер"
                text = format_digest(name, deals, top_n=5)
                await send_to_user(user.tg_user_id, text)
            except Exception as exc:
                logger.exception("Hermes digest failed for user=%d: %s", user.tg_user_id, exc)

    except Exception as exc:
        logger.exception("Hermes morning digest job failed: %s", exc)


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

    # Ежедневный отчёт за вчера — каждый день в 09:00 по Ташкенту
    _scheduler.add_job(
        daily_report_job,
        trigger=CronTrigger(hour=9, minute=0, timezone="Asia/Tashkent"),
        id="daily_report",
        name="Daily yesterday report",
        replace_existing=True,
    )

    # Обновление токена AmoCRM каждые 30 минут (порог обновления — 2 часа до истечения)
    _scheduler.add_job(
        refresh_amocrm_token_job,
        trigger="interval",
        minutes=30,
        id="amocrm_token_refresh",
        name="AmoCRM token refresh",
        replace_existing=True,
    )

    # Hermes: персональный дайджест менеджерам каждый день в hermes_digest_hour
    _scheduler.add_job(
        hermes_morning_digest_job,
        trigger=CronTrigger(hour=settings.hermes_digest_hour, minute=0, timezone=settings.timezone),
        id="hermes_morning_digest",
        name="Hermes morning digest",
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
