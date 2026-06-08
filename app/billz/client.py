"""
BILLZ POS API клиент.

Аутентификация:
  POST /v1/auth/login   — логин по secret_token (без platform-id header)
  POST /v2/auth/refresh — refresh по refresh_token (с platform-id header)

Токен хранится в таблице billz_token (БД) и кешируется in-memory.
Refresh под asyncio.Lock с double-check (порт паттерна AmocrmClient).
Rate-limit: 2 req/sec — пауза 550ms между запросами (порт Code.gs).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_RATE_DELAY = 0.55          # 550 мс → ~1.8 req/sec (безопасно под лимит 2 req/sec)
_REFRESH_MARGIN = 300       # обновлять токен за 5 минут до истечения

# Защита от параллельных обновлений токена
_token_lock = asyncio.Lock()
# Семафор rate-limit: один запрос за раз + пауза
_rate_sem = asyncio.Semaphore(1)

# In-memory кэш токена
_cached_token: Optional[str] = None
_cached_expires_at: Optional[datetime] = None
_cached_refresh: Optional[str] = None


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def _needs_refresh(expires_at: Optional[datetime]) -> bool:
    """True если токен истекает через <REFRESH_MARGIN сек или не задан."""
    if expires_at is None:
        return True
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return expires_at - now < timedelta(seconds=_REFRESH_MARGIN)


async def _do_login() -> dict:
    """POST /v1/auth/login — без platform-id header."""
    if not settings.billz_secret:
        raise RuntimeError("BILLZ_SECRET не задан в .env")
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{settings.billz_api_url}/v1/auth/login",
            json={"secret_token": settings.billz_secret},
        )
        resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 200:
        raise RuntimeError(f"BILLZ login failed: {body.get('message')}")
    return body["data"]


async def _do_refresh(refresh_token: str) -> dict:
    """POST /v2/auth/refresh — с platform-id header."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{settings.billz_api_url}/v2/auth/refresh",
            json={"refresh_token": refresh_token},
            headers={"platform-id": settings.billz_platform_id},
        )
        resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 200:
        raise RuntimeError(f"BILLZ refresh failed: {body.get('message')}")
    return body["data"]


async def _persist_token(data: dict) -> str:
    """Сохраняет токен в БД и обновляет in-memory кэш. Возвращает access_token."""
    global _cached_token, _cached_expires_at, _cached_refresh

    expires_in = data.get("expires_in", 86400)
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=expires_in)

    try:
        from app.db import get_session
        from app.repository import upsert_billz_token
        async with get_session() as session:
            await upsert_billz_token(
                session,
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_at=expires_at,
            )
    except Exception as exc:
        logger.warning("BILLZ: could not persist token to DB: %s", exc)

    _cached_token = data["access_token"]
    _cached_expires_at = expires_at
    _cached_refresh = data["refresh_token"]
    return _cached_token


# ---------------------------------------------------------------------------
# Получение валидного токена
# ---------------------------------------------------------------------------

async def get_valid_token() -> str:
    """
    Возвращает валидный access_token.
    Стратегия: in-memory кэш → DB → refresh → полный login.
    Refresh происходит под asyncio.Lock с double-check.
    """
    global _cached_token, _cached_expires_at, _cached_refresh

    # Быстрый путь без лока
    if _cached_token and not _needs_refresh(_cached_expires_at):
        return _cached_token

    async with _token_lock:
        # Double-check после захвата лока
        if _cached_token and not _needs_refresh(_cached_expires_at):
            return _cached_token

        # Подгрузить из БД если кэш пустой
        if _cached_token is None:
            try:
                from app.db import get_session
                from app.repository import get_billz_token
                async with get_session() as session:
                    row = await get_billz_token(session)
                if row:
                    if not _needs_refresh(row.expires_at):
                        _cached_token = row.access_token
                        _cached_expires_at = row.expires_at
                        _cached_refresh = row.refresh_token
                        logger.debug("BILLZ: loaded token from DB")
                        return _cached_token
                    # Токен в БД тоже истёк — запомним refresh для следующего шага
                    _cached_refresh = row.refresh_token
            except Exception as exc:
                logger.warning("BILLZ: DB token load failed: %s", exc)

        # Попытка refresh
        if _cached_refresh:
            try:
                logger.info("BILLZ: refreshing token…")
                data = await _do_refresh(_cached_refresh)
                return await _persist_token(data)
            except Exception as exc:
                logger.warning("BILLZ: refresh failed (%s), will do full login", exc)
                _cached_refresh = None

        # Полный логин
        logger.info("BILLZ: performing full login…")
        data = await _do_login()
        return await _persist_token(data)


# ---------------------------------------------------------------------------
# Базовый HTTP запрос
# ---------------------------------------------------------------------------

async def _request(
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    json: Optional[dict] = None,
    _retry: bool = True,
) -> Any:
    """
    HTTP-запрос к BILLZ с rate-limiting и авто-ретраем на 401.
    """
    token = await get_valid_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "accept": "application/json",
        "platform-id": settings.billz_platform_id,
    }
    url = settings.billz_api_url + path

    async with _rate_sem:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(
                method, url, headers=headers, params=params or {}, json=json
            )
        # Пауза для соблюдения rate-limit в 2 req/sec
        await asyncio.sleep(_RATE_DELAY)

    if resp.status_code == 401 and _retry:
        logger.warning("BILLZ 401 on %s — resetting token and retrying once", path)
        global _cached_token, _cached_expires_at
        _cached_token = None
        _cached_expires_at = None
        return await _request(method, path, params=params, json=json, _retry=False)

    resp.raise_for_status()
    return resp.json()


async def get(path: str, params: Optional[dict] = None) -> Any:
    """GET-запрос к BILLZ API."""
    return await _request("GET", path, params=params)


async def post(path: str, json: Optional[dict] = None) -> Any:
    """POST-запрос к BILLZ API."""
    return await _request("POST", path, json=json)
