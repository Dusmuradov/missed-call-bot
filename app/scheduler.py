"""
APScheduler: периодические задачи.

Джобы:
  - check_callback_escalations: каждые N минут проверяет пропущенные звонки
    без перезвона и отправляет эскалации в Telegram.
  - weekly_report_job: каждый понедельник в 09:00 — AI-дайджест за прошлую неделю.
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


async def amocrm_token_refresh_job() -> None:
    """Обновляет AmoCRM access_token каждые 15 минут (буфер для 20-минутного токена)."""
    from app.amocrm.client import refresh_tokens
    result = await refresh_tokens()
    if result:
        logger.info("AmoCRM token auto-refreshed successfully.")
    else:
        logger.debug("AmoCRM token refresh skipped (нет refresh_token в БД или OAuth не настроен).")


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



async def _send_to_managers(text: str) -> None:
    """Отправляет сообщение всем пользователям с ролью manager + администратору."""
    from app.db import get_session
    from app.repository import list_bot_users
    from app.telegram import send_to_user

    recipients: set[int] = set()
    if settings.admin_user_id:
        recipients.add(settings.admin_user_id)

    try:
        async with get_session() as session:
            managers = await list_bot_users(session, role="manager")
        for u in managers:
            recipients.add(u.tg_user_id)
    except Exception as exc:
        logger.error("_send_to_managers: could not load manager list: %s", exc)

    for uid in recipients:
        await send_to_user(uid, text)


async def daily_report_job() -> None:
    """
    Еженедельный AI-дайджест — каждый понедельник в 09:00.
    Данные: BILLZ (продажи + остатки) + AmoCRM (лиды по менеджерам) + Utel (звонки).
    AI анализирует всё вместе и выдаёт план + рекомендации на текущую неделю.
    """
    from zoneinfo import ZoneInfo

    from app.amocrm.reports import get_lead_metrics_by_users
    from app.analytics_utel import get_period_stats
    from app.billz import aggregator as agg
    from app.billz import reports as billz_reports
    from app.billz.weekly_ai import analyze_weekly, format_weekly_digest
    from app.db import get_session
    from app.periods import period_last_week

    logger.info("Running weekly AI digest…")
    from_utc, to_utc = period_last_week()
    tz_name = settings.timezone
    tz = ZoneInfo(tz_name)

    from_local = from_utc.replace(tzinfo=__import__("datetime").timezone.utc).astimezone(tz)
    to_local   = to_utc.replace(tzinfo=__import__("datetime").timezone.utc).astimezone(tz)
    period_label = f"{from_local.strftime('%d.%m')} – {to_local.strftime('%d.%m.%Y')}"

    date_from = from_local.strftime("%Y-%m-%d")
    date_to   = to_local.strftime("%Y-%m-%d")

    # ── 1. BILLZ данные ──
    billz_kpi: dict | None = None
    if settings.billz_secret and settings.billz_company_id:
        try:
            order_details: list[dict] = []
            async for order in billz_reports.iter_orders(date_from, date_to):
                detail = await billz_reports.get_order_detail(order["id"])
                if detail:
                    order_details.append(billz_reports.parse_order_detail(detail))

            billz_kpi = agg.aggregate(order_details, period_label=period_label)

            stock_res, imports_res, prod_sales_res = await asyncio.gather(
                billz_reports.get_stock(date_to),
                billz_reports.get_imports(date_from, date_to),
                billz_reports.get_product_sales(date_from, date_to),
                return_exceptions=True,
            )
            if not isinstance(stock_res, Exception) and stock_res:
                billz_kpi["stock"] = agg.aggregate_stock(stock_res, billz_kpi.get("velocity") or {})
            if not isinstance(imports_res, Exception):
                billz_kpi["imports"] = agg.aggregate_imports(imports_res or [])
            if not isinstance(prod_sales_res, Exception):
                billz_kpi["product_sales"] = agg.aggregate_product_sales(prod_sales_res or [])

            logger.info("BILLZ weekly: %d orders for %s–%s", len(order_details), date_from, date_to)
        except Exception as exc:
            logger.error("BILLZ weekly data failed: %s", exc)

    # ── 2. AmoCRM лиды ──
    amo_data: dict | None = None
    try:
        amo_data = await get_lead_metrics_by_users(from_utc, to_utc, tz_name=tz_name)
    except Exception as exc:
        logger.error("AmoCRM weekly data failed: %s", exc)

    # ── 3. Utel звонки ──
    utel_flat: dict | None = None
    try:
        async with get_session() as session:
            stats = await get_period_stats(session, from_utc, to_utc, tz_name=tz_name)
        total = stats.get("total", 0)
        incoming = stats.get("incoming", 0)
        missed = stats.get("missed", 0)
        outgoing = stats.get("outgoing", 0)
        utel_flat = {
            "total": total,
            "incoming": incoming,
            "missed": missed,
            "outgoing": outgoing,
            "missed_rate": round(missed / incoming * 100, 1) if incoming else 0,
        }
    except Exception as exc:
        logger.error("Utel weekly data failed: %s", exc)

    # ── 4. AI-анализ ──
    ai_result = await analyze_weekly(period_label, billz_kpi, amo_data, utel_flat)

    # ── 5. Форматирование и отправка ──
    messages = format_weekly_digest(ai_result, period_label)
    for msg in messages:
        await _send_to_managers(msg)

    logger.info("Weekly AI digest sent (%d messages).", len(messages))


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


async def billz_daily_digest_job() -> None:
    """
    Ежедневный POS-дайджест из BILLZ — отправляется менеджерам и администратору в 09:05.
    Пайплайн: BILLZ API → aggregate → DeepSeek AI → Telegram.
    """
    from zoneinfo import ZoneInfo

    from app.billz import aggregator as agg
    from app.billz import ai as billz_ai
    from app.billz import reports
    from app.db import get_session
    from app.formatting import build_billz_digest
    from app.repository import get_billz_snapshot, save_billz_snapshot

    logger.info("BILLZ daily digest job starting…")
    tz = ZoneInfo(settings.timezone)

    # Дата «вчера» в локальной таймзоне
    from datetime import datetime, timezone as _tz
    now_local = datetime.now(_tz.utc).astimezone(tz)
    yesterday = (now_local - __import__("datetime").timedelta(days=1)).date()
    date_str = yesterday.strftime("%Y-%m-%d")
    period_label = yesterday.strftime("%d.%m.%Y")

    # Загрузить снимок «позавчера» для сравнения
    day_before = (yesterday - __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        async with get_session() as session:
            prev_row = await get_billz_snapshot(session, day_before)
        prev_snapshot = (
            {"revenue": prev_row.revenue, "orders": prev_row.orders,
             "aov": prev_row.aov, "items_sold": prev_row.items_sold}
            if prev_row else None
        )
    except Exception as exc:
        logger.warning("BILLZ: could not load prev snapshot: %s", exc)
        prev_snapshot = None

    # Получить и распарсить заказы за вчера
    order_details: list[dict] = []
    try:
        async for order in reports.iter_orders(date_str, date_str):
            detail = await reports.get_order_detail(order["id"])
            if detail:
                order_details.append(reports.parse_order_detail(detail))
        logger.info("BILLZ: fetched %d orders for %s", len(order_details), date_str)
    except Exception as exc:
        logger.error("BILLZ: order fetch failed: %s", exc)
        await _send_to_managers(
            f"📊 <b>BILLZ дайджест {period_label}</b>\n⚠️ Ошибка получения данных: {exc}"
        )
        return

    # Агрегация KPI продаж
    kpi = agg.aggregate(order_details, period_label=period_label, prev_snapshot=prev_snapshot)

    # Параллельный сбор всех отчётных данных
    stock_res, imports_res, prod_sales_res, sup_sales_res, summary_res = await asyncio.gather(
        reports.get_stock(date_str),
        reports.get_imports(date_str, date_str),
        reports.get_product_sales(date_str, date_str),
        reports.get_supplier_sales(date_str, date_str),
        reports.get_summary(date_str, date_str),
        return_exceptions=True,
    )

    if isinstance(stock_res, Exception):
        logger.warning("BILLZ: stock failed: %s", stock_res)
        kpi["stock"] = None
    elif stock_res:
        kpi["stock"] = agg.aggregate_stock(stock_res, kpi.get("velocity") or {})
        logger.info("BILLZ: stock aggregated (%d rows)", len(stock_res))

    if isinstance(imports_res, Exception):
        logger.warning("BILLZ: imports failed: %s", imports_res)
        kpi["imports"] = None
    else:
        kpi["imports"] = agg.aggregate_imports(imports_res or [])
        logger.info("BILLZ: imports aggregated (%d rows)", len(imports_res or []))

    if isinstance(prod_sales_res, Exception):
        logger.warning("BILLZ: product-sales failed: %s", prod_sales_res)
        kpi["product_sales"] = None
    else:
        kpi["product_sales"] = agg.aggregate_product_sales(prod_sales_res or [])
        logger.info("BILLZ: product-sales aggregated (%d rows)", len(prod_sales_res or []))

    if isinstance(sup_sales_res, Exception):
        logger.warning("BILLZ: supplier-sales failed: %s", sup_sales_res)
        kpi["supplier_sales"] = None
    else:
        kpi["supplier_sales"] = agg.aggregate_supplier_sales(sup_sales_res or [])
        logger.info("BILLZ: supplier-sales aggregated (%d rows)", len(sup_sales_res or []))

    kpi["summary"] = None if isinstance(summary_res, Exception) else summary_res
    if isinstance(summary_res, Exception):
        logger.warning("BILLZ: summary failed: %s", summary_res)

    # Сохранить снимок текущего дня
    try:
        snap = agg.snapshot_from_kpi(kpi)
        async with get_session() as session:
            await save_billz_snapshot(
                session,
                snapshot_date=date_str,
                revenue=snap["revenue"],
                orders=snap["orders"],
                aov=snap["aov"],
                items_sold=snap["items_sold"],
            )
    except Exception as exc:
        logger.warning("BILLZ: could not save snapshot: %s", exc)

    # AI-анализ
    try:
        ai_blocks = await billz_ai.analyze(kpi)
    except Exception as exc:
        logger.error("BILLZ: AI analysis failed: %s", exc)
        ai_blocks = billz_ai._fallback(str(exc))

    # Форматирование и отправка
    messages = build_billz_digest(kpi, ai_blocks)
    for msg in messages:
        await _send_to_managers(msg)

    logger.info("BILLZ daily digest sent (%d messages).", len(messages))


async def run_billz_digest_now() -> None:
    """Ручной запуск дайджеста для текущей даты (для /billz/run-daily эндпоинта)."""
    await billz_daily_digest_job()


async def rop_daily_plan_job() -> None:
    """
    Ежедневный план P1/P2/P3 — каждый день в rop_plan_hour:15 (Asia/Tashkent).
    Sellers → персональный план → send_to_user.
    Managers + admin → командная сводка РОП → _send_to_managers.
    """
    if not settings.deepseek_api_key:
        logger.debug("ROP plan skipped: DEEPSEEK_API_KEY not set.")
        return
    if not settings.amocrm_long_lived_token:
        logger.debug("ROP plan skipped: AMOCRM_LONG_LIVED_TOKEN not set.")
        return

    try:
        from app.db import get_session
        from app.repository import list_bot_users
        from app.telegram import send_to_user
        from hermes.daily_plan import build_daily_plan
        from hermes.rop_rollup import build_rop_rollup

        async with get_session() as session:
            all_users = await list_bot_users(session)

        sellers = [u for u in all_users if u.amocrm_user_id and u.role == "seller"]
        logger.info("ROP daily plan: %d sellers", len(sellers))

        for user in sellers:
            try:
                text = await build_daily_plan(user.amocrm_user_id, user.tg_user_id)
                await send_to_user(user.tg_user_id, text)
            except Exception as exc:
                logger.exception("ROP plan failed for user=%d: %s", user.tg_user_id, exc)

        try:
            rollup = await build_rop_rollup()
            await _send_to_managers(rollup)
        except Exception as exc:
            logger.exception("ROP rollup failed: %s", exc)

    except Exception as exc:
        logger.exception("ROP daily plan job failed: %s", exc)


async def run_rop_plan_now() -> None:
    """Ручной запуск ROP-плана (для /rop/run-plan эндпоинта)."""
    await rop_daily_plan_job()


def create_scheduler() -> AsyncIOScheduler:
    """Создаёт и настраивает планировщик."""
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone="UTC")

    # Обновление AmoCRM токена каждые 15 минут
    if settings.amocrm_client_id and settings.amocrm_client_secret:
        _scheduler.add_job(
            amocrm_token_refresh_job,
            trigger="interval",
            minutes=15,
            id="amocrm_token_refresh",
            name="AmoCRM OAuth token auto-refresh",
            replace_existing=True,
        )
        logger.info("AmoCRM token auto-refresh scheduled every 15 min.")

    # Проверка перезвонов каждые N минут
    _scheduler.add_job(
        check_callback_escalations,
        trigger="interval",
        minutes=settings.callback_check_minutes,
        id="callback_escalation",
        name="Missed call callback escalation",
        replace_existing=True,
    )

    # Еженедельный отчёт за прошлую неделю — каждый понедельник в 09:00 по Ташкенту
    _scheduler.add_job(
        daily_report_job,
        trigger=CronTrigger(day_of_week="mon", hour=9, minute=0, timezone="Asia/Tashkent"),
        id="weekly_report",
        name="Weekly last-week report",
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

    # ROP: ежедневный план P1/P2/P3 продавцам + сводка менеджерам
    _scheduler.add_job(
        rop_daily_plan_job,
        trigger=CronTrigger(
            hour=settings.rop_plan_hour, minute=15, timezone=settings.timezone
        ),
        id="rop_daily_plan",
        name="ROP daily P1/P2/P3 plan",
        replace_existing=True,
    )
    logger.info(
        "ROP daily plan scheduled at %02d:15 %s",
        settings.rop_plan_hour,
        settings.timezone,
    )

    # BILLZ: ежедневный POS-дайджест в billz_digest_hour
    if settings.billz_secret and settings.billz_company_id:
        _scheduler.add_job(
            billz_daily_digest_job,
            trigger=CronTrigger(
                hour=settings.billz_digest_hour, minute=5, timezone=settings.timezone
            ),
            id="billz_daily_digest",
            name="BILLZ daily POS digest",
            replace_existing=True,
        )
        logger.info(
            "BILLZ daily digest scheduled at %02d:05 %s",
            settings.billz_digest_hour,
            settings.timezone,
        )
    else:
        logger.info("BILLZ daily digest NOT scheduled: BILLZ_SECRET or BILLZ_COMPANY_ID not set")

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
