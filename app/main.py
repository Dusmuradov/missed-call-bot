from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone  # timezone used in webhook handler
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
        "crm_base_url": settings.crm_base_url or "(not set)",
        "crm_username": bool(settings.crm_username),
        "crm_password": bool(settings.crm_password),
    }


@app.get("/crm/auth/test", tags=["crm"])
async def crm_auth_test() -> dict:
    """Тестирует CRM авторизацию: login → access_token → M2M token."""
    if not settings.crm_base_url:
        return {"error": "CRM_BASE_URL не задан в .env"}
    try:
        from app.crm.client import get_valid_m2m_token
        m2m = await get_valid_m2m_token()
        return {"ok": True, "m2m_token_prefix": m2m[:12] + "…"}
    except Exception as exc:
        logger.error("CRM auth test failed: %s", exc)
        return {"ok": False, "error": str(exc)}


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



# ---------------------------------------------------------------------------
# BILLZ debug routes
# ---------------------------------------------------------------------------

@app.get("/admin/seed-users", tags=["meta"])
async def seed_users() -> dict:
    """
    Восстанавливает известных пользователей в пустой БД после деплоя.
    Идемпотентно: если пользователь уже есть — пропускает.
    Доступен без авторизации — использовать только сразу после деплоя.
    """
    from app.db import get_session
    from app.repository import create_pending_user, get_bot_user, set_user_role

    SEED_USERS = [
        {"tg_user_id": 633535801, "username": "azizdusmuradov", "full_name": "Aziz", "role": "admin"},
        {"tg_user_id": 701287094, "username": "mr_rashidovs", "full_name": "Rashidov", "role": "manager"},
        {"tg_user_id": 1322668592, "username": "Boburkhoja_Neo", "full_name": "Boburkhoja Neo", "role": "manager"},
    ]

    results = []
    for u in SEED_USERS:
        async with get_session() as session:
            existing = await get_bot_user(session, u["tg_user_id"])
            if existing is not None:
                results.append({"tg_user_id": u["tg_user_id"], "status": "already_exists", "role": existing.role})
                continue
            await create_pending_user(
                session,
                tg_user_id=u["tg_user_id"],
                username=u["username"],
                full_name=u["full_name"],
            )
        async with get_session() as session:
            await set_user_role(
                session, u["tg_user_id"], u["role"],
                approved_by=633535801,
            )
        results.append({"tg_user_id": u["tg_user_id"], "status": "created", "role": u["role"]})

    return {"ok": True, "seeded": results}


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
