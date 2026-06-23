"""
CRM auth сервис: username/password → access_token → M2M token.

Поток:
  1. POST {CRM_BASE_URL}{CRM_LOGIN_PATH}  {username, password}  → access_token
  2. POST {CRM_BASE_URL}{CRM_M2M_PATH}    {access_token}         → m2m_token + expires_in
  3. Все API-запросы используют m2m_token в заголовке Authorization.

Токены кешируются in-memory + в таблице crm_token (БД).
Обновление под asyncio.Lock с double-check (аналог billz/client.py).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_REFRESH_MARGIN = 300  # обновлять m2m_token за 5 минут до истечения

_token_lock = asyncio.Lock()

_cached_access: Optional[str] = None
_cached_m2m: Optional[str] = None
_cached_m2m_expires: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def _m2m_needs_refresh(expires_at: Optional[datetime]) -> bool:
    if expires_at is None:
        return True
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return expires_at - now < timedelta(seconds=_REFRESH_MARGIN)


def _url(path: str) -> str:
    base = settings.crm_base_url.rstrip("/")
    return base + path


# ---------------------------------------------------------------------------
# Шаг 1: username + password → access_token
# ---------------------------------------------------------------------------

async def _do_login() -> str:
    """POST логин по username/password. Возвращает access_token."""
    if not settings.crm_base_url:
        raise RuntimeError("CRM_BASE_URL не задан в .env")
    if not settings.crm_username or not settings.crm_password:
        raise RuntimeError("CRM_USERNAME / CRM_PASSWORD не заданы в .env")

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            _url(settings.crm_login_path),
            json={"username": settings.crm_username, "password": settings.crm_password},
        )
        resp.raise_for_status()

    body = resp.json()
    token = (
        body.get("access_token")
        or body.get("accessToken")
        or body.get("token")
        or (body.get("data") or {}).get("access_token")
        or (body.get("data") or {}).get("token")
    )
    if not token:
        raise RuntimeError(f"CRM login: access_token не найден в ответе: {body}")
    logger.info("CRM: login successful")
    return token


# ---------------------------------------------------------------------------
# Шаг 2: access_token → M2M token
# ---------------------------------------------------------------------------

async def _do_fetch_m2m(access_token: str) -> dict:
    """POST c access_token → {m2m_token, expires_in}."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            _url(settings.crm_m2m_path),
            json={"access_token": access_token},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()

    body = resp.json()
    data = body.get("data") or body
    m2m = (
        data.get("m2m_token")
        or data.get("m2mToken")
        or data.get("token")
        or data.get("access_token")
    )
    if not m2m:
        raise RuntimeError(f"CRM M2M: m2m_token не найден в ответе: {body}")
    expires_in = data.get("expires_in", 86400)
    logger.info("CRM: M2M token fetched, expires_in=%s", expires_in)
    return {"m2m_token": m2m, "expires_in": expires_in}


# ---------------------------------------------------------------------------
# Сохранение токенов
# ---------------------------------------------------------------------------

async def _persist(access_token: str, m2m_token: str, expires_in: int) -> str:
    global _cached_access, _cached_m2m, _cached_m2m_expires

    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=expires_in)
    try:
        from app.db import get_session
        from app.repository import upsert_crm_token
        async with get_session() as session:
            await upsert_crm_token(
                session,
                access_token=access_token,
                m2m_token=m2m_token,
                m2m_expires_at=expires_at,
            )
    except Exception as exc:
        logger.warning("CRM: не удалось сохранить токен в БД: %s", exc)

    _cached_access = access_token
    _cached_m2m = m2m_token
    _cached_m2m_expires = expires_at
    return m2m_token


# ---------------------------------------------------------------------------
# Публичный интерфейс
# ---------------------------------------------------------------------------

async def get_valid_m2m_token() -> str:
    """
    Возвращает валидный M2M токен.
    Стратегия: in-memory кэш → DB → полный login + fetch_m2m.
    Обновление под asyncio.Lock с double-check.
    """
    global _cached_access, _cached_m2m, _cached_m2m_expires

    if _cached_m2m and not _m2m_needs_refresh(_cached_m2m_expires):
        return _cached_m2m

    async with _token_lock:
        if _cached_m2m and not _m2m_needs_refresh(_cached_m2m_expires):
            return _cached_m2m

        # Попробовать загрузить из БД
        if _cached_m2m is None:
            try:
                from app.db import get_session
                from app.repository import get_crm_token
                async with get_session() as session:
                    row = await get_crm_token(session)
                if row and row.m2m_token and not _m2m_needs_refresh(row.m2m_expires_at):
                    _cached_access = row.access_token
                    _cached_m2m = row.m2m_token
                    _cached_m2m_expires = row.m2m_expires_at
                    logger.debug("CRM: loaded M2M token from DB")
                    return _cached_m2m
                if row:
                    _cached_access = row.access_token
            except Exception as exc:
                logger.warning("CRM: DB token load failed: %s", exc)

        # M2M истёк или отсутствует — перелогиниться и получить новый
        logger.info("CRM: obtaining new M2M token…")
        access_token = await _do_login()
        m2m_data = await _do_fetch_m2m(access_token)
        return await _persist(access_token, m2m_data["m2m_token"], m2m_data["expires_in"])


async def request(
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    json: Optional[dict] = None,
    _retry: bool = True,
) -> Any:
    """
    HTTP-запрос к CRM API с авто-обновлением M2M токена на 401.
    """
    global _cached_m2m, _cached_m2m_expires

    token = await get_valid_m2m_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    url = _url(path)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(method, url, headers=headers, params=params or {}, json=json)

    if resp.status_code == 401 and _retry:
        logger.warning("CRM 401 на %s — сбрасываем токен и повторяем", path)
        _cached_m2m = None
        _cached_m2m_expires = None
        return await request(method, path, params=params, json=json, _retry=False)

    resp.raise_for_status()
    return resp.json()


async def get(path: str, params: Optional[dict] = None) -> Any:
    return await request("GET", path, params=params)


async def post(path: str, json: Optional[dict] = None) -> Any:
    return await request("POST", path, json=json)
