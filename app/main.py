from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.config import settings
from app.formatting import build_message
from app.logging_conf import setup_logging
from app.operators import get_operator_name
from app.schemas import UtelWebhook
from app.security import verify_webhook_secret
from app.telegram import close_bot, send_notification, start_polling, stop_polling

setup_logging(settings.log_level)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Missed-call bot starting up…")

    # 1. Инициализировать БД
    from app.db import close_db, init_db
    await init_db()

    # 2. Запустить планировщик (проверка перезвонов каждые N минут)
    from app.scheduler import start_scheduler, stop_scheduler
    start_scheduler()

    # 3. Запустить long-polling бота (inline-меню и кнопки)
    await start_polling()

    yield

    # Shutdown
    logger.info("Missed-call bot shutting down…")
    await stop_polling()
    stop_scheduler()
    await close_bot()
    await close_db()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Missed Call Bot",
    description="Utel.uz missed-call notifications + analytics → Telegram",
    version="2.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Routes: meta
# ---------------------------------------------------------------------------

@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {"status": "alive", "service": "missed-call-bot"}


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}



# ---------------------------------------------------------------------------
# AmoCRM OAuth (первичная авторизация — одноразово)
# ---------------------------------------------------------------------------

@app.get("/debug/config", tags=["meta"])
async def debug_config() -> dict:
    """Показывает статус ключевых переменных (без значений) для диагностики Railway."""
    return {
        "amocrm_long_lived_token": bool(settings.amocrm_long_lived_token),
        "amocrm_subdomain": settings.amocrm_subdomain or "(not set)",
        "deepseek_api_key": bool(settings.deepseek_api_key),
        "billz_secret": bool(settings.billz_secret),
        "billz_company_id": bool(settings.billz_company_id),
        "bot_token": bool(settings.bot_token),
        "telegram_chat_id": bool(settings.telegram_chat_id),
    }


@app.get("/amocrm/users", tags=["amocrm"])
async def amocrm_users() -> dict:
    """Список пользователей AmoCRM с id и email."""
    from app.amocrm.client import get_valid_client
    client = await get_valid_client()
    if client is None:
        return {"error": "AmoCRM не авторизован"}
    users = await client.get_users()
    return {"users": [{"id": u.get("id"), "name": u.get("name"), "email": u.get("email")} for u in users]}


@app.get("/amocrm/pipelines", tags=["amocrm"])
async def amocrm_pipelines() -> dict:
    """Показывает воронки и статусы — для настройки AMOCRM_INITIAL_STATUS_ID."""
    from app.amocrm.client import get_valid_client
    client = await get_valid_client()
    if client is None:
        return {"error": "AmoCRM не авторизован"}
    pipelines = await client.get_pipelines()
    result = []
    for pl in pipelines:
        statuses = pl.get("_embedded", {}).get("statuses", [])
        statuses.sort(key=lambda s: s.get("sort", 9999))
        result.append({
            "pipeline_id": pl.get("id"),
            "pipeline_name": pl.get("name"),
            "statuses": [
                {"id": s.get("id"), "name": s.get("name"), "sort": s.get("sort")}
                for s in statuses
            ],
        })
    return {"pipelines": result}


@app.get("/amocrm/oauth/start", tags=["amocrm"])
async def amocrm_oauth_start() -> dict:
    """Возвращает ссылку для OAuth-авторизации AmoCRM."""
    if not settings.amocrm_subdomain:
        return {"error": "AMOCRM_SUBDOMAIN not configured"}

    import secrets
    state = secrets.token_urlsafe(16)
    url = (
        f"https://www.amocrm.ru/oauth"
        f"?client_id={settings.amocrm_client_id}"
        f"&state={state}"
        f"&redirect_uri={settings.amocrm_redirect_uri}"
        f"&response_type=code"
        f"&mode=redirect"
    )
    return {"oauth_url": url, "state": state}


@app.get("/amocrm/oauth/callback", tags=["amocrm"])
async def amocrm_oauth_callback(code: str, state: str = "") -> dict:
    """Обменивает OAuth-код на токены и сохраняет в БД."""
    from datetime import timedelta

    from app.amocrm.client import exchange_code
    from app.db import get_session
    from app.repository import upsert_amocrm_token

    try:
        tokens = await exchange_code(code)
    except Exception as exc:
        logger.error("AmoCRM OAuth exchange failed: %s", exc)
        return {"error": str(exc)}

    expires_in = tokens.get("expires_in", 86400)
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=expires_in)

    async with get_session() as session:
        await upsert_amocrm_token(
            session,
            subdomain=settings.amocrm_subdomain,
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            expires_at=expires_at,
        )

    logger.info("AmoCRM tokens saved for subdomain=%s", settings.amocrm_subdomain)
    return {"ok": True, "subdomain": settings.amocrm_subdomain}


# ---------------------------------------------------------------------------
# BILLZ debug routes
# ---------------------------------------------------------------------------

@app.get("/billz/run-daily", tags=["billz"])
async def billz_run_daily() -> dict:
    """
    Ручной запуск BILLZ-дайджеста за вчера.
    Используется для тестирования без ожидания расписания (09:05).
    """
    if not settings.billz_secret or not settings.billz_company_id:
        return {"error": "BILLZ_SECRET или BILLZ_COMPANY_ID не заданы в .env"}
    import asyncio
    from app.scheduler import run_billz_digest_now
    asyncio.create_task(run_billz_digest_now())
    return {"ok": True, "message": "BILLZ дайджест запущен в фоне — проверьте Telegram"}


# ---------------------------------------------------------------------------
# Webhook Utel
# ---------------------------------------------------------------------------

@app.post(
    "/webhook/utel",
    tags=["webhook"],
    dependencies=[Depends(verify_webhook_secret)],
)
async def utel_webhook(request: Request) -> JSONResponse:
    """
    Принимает событие звонка от Utel.uz.

    Порядок обработки:
    1. Проверка секрета (Depends).
    2. Чтение и парсинг тела (JSON / form).
    3. Нормализация в UtelWebhook.
    4. Фильтрация не-call_saved событий.
    5. Сохранение звонка в БД (дедупликация через unique call_id).
    6. Если входящий пропущенный → создать missed_tracking + уведомление.
    7. Если исходящий → матчинг перезвона по номеру.
    """
    # --- Шаг 2: читаем тело ---
    content_type = request.headers.get("content-type", "")
    raw_data: Any = {}

    try:
        if "application/json" in content_type:
            raw_data = await request.json()
        elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            form = await request.form()
            raw_data = dict(form)
        else:
            try:
                raw_data = await request.json()
            except Exception:
                raw_data = {}
    except Exception as exc:
        logger.warning("Failed to parse request body: %s", exc)
        raw_data = {}

    logger.debug("Raw webhook payload: %s", raw_data)

    # --- Шаг 3: нормализация ---
    event = UtelWebhook.model_validate(raw_data)

    # --- Шаг 4: фильтрация не-call_saved ---
    if not event.is_target_event:
        logger.debug("Skipping event: %s", event.event_name)
        return JSONResponse({"skipped": True, "reason": "not_target_event"})

    # --- Шаг 5: сохранение в БД ---
    operator_name = get_operator_name(event.operator_ext)
    call_time_utc = event.call_time_utc
    direction = event.normalized_direction
    answered = event.is_answered_call

    from app.db import get_session
    from app.repository import (
        create_missed_tracking,
        find_open_missed,
        mark_called_back,
        save_call,
    )

    call_obj = None
    async with get_session() as session:
        call_obj = await save_call(
            session,
            call_id=event.call_id,
            direction=direction,
            external_number=event.caller,
            operator_ext=event.operator_ext,
            operator_name=operator_name,
            call_time_utc=call_time_utc,
            status_name=event.status_name,
            status_number=event.status_number,
            wait_seconds=event.wait_seconds,
            answered=answered,
            raw=raw_data if isinstance(raw_data, dict) else {},
        )

    if call_obj is None:
        # Дубль call_id — уже обрабатывали
        logger.info("Duplicate call_id=%s ignored.", event.call_id)
        return JSONResponse({"skipped": True, "reason": "duplicate"})

    # --- Шаг 6: входящий пропущенный ---
    if event.is_missed:
        missed_at = call_time_utc or datetime.now(timezone.utc).replace(tzinfo=None)

        async with get_session() as session:
            await create_missed_tracking(
                session,
                call=call_obj,
                external_number=event.caller,
                operator_ext=event.operator_ext,
                missed_at_utc=missed_at,
            )

        text = build_message(
            caller=event.caller,
            call_time=event.call_time,
            wait_seconds=event.wait_seconds,
            timezone_str=settings.timezone,
            operator_name=operator_name,
        )
        sent = await send_notification(text)

        if sent:
            logger.info(
                "Missed call from %s notified. call_id=%s wait=%ss",
                event.caller, event.call_id, event.wait_seconds,
            )
        else:
            logger.error("Failed to send Telegram notification for call from %s", event.caller)

        return JSONResponse({"ok": True, "sent": sent, "type": "missed"})

    # --- Шаг 7: исходящий → матчинг перезвона ---
    if event.is_outgoing and event.caller:
        async with get_session() as session:
            open_missed = await find_open_missed(session, external_number=event.caller)

        if open_missed:
            cb_by = operator_name or event.operator_ext or "Оператор"
            async with get_session() as session:
                for tracking in open_missed:
                    from app.repository import get_tracking_by_id
                    t = await get_tracking_by_id(session, tracking.id)
                    if t:
                        await mark_called_back(session, t, called_back_by=cb_by)

            logger.info(
                "Callback matched: %d missed trackings closed for number=%s by %s",
                len(open_missed), event.caller, cb_by,
            )
            return JSONResponse({"ok": True, "type": "callback_matched", "count": len(open_missed)})

    # Остальные события (входящие отвеченные и т.п.) — просто записаны в БД
    logger.debug("Call recorded: direction=%s status=%s caller=%s", direction, event.status_name, event.caller)
    return JSONResponse({"ok": True, "type": "recorded"})
