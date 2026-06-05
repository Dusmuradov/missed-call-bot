"""
AmoCRM REST v4 клиент для одного аккаунта.

Токен хранится в таблице amocrm_token (БД).
Первичная OAuth-авторизация: GET /amocrm/oauth/callback?code=...&state=...
(открыть браузером после настройки webhook в личном кабинете AmoCRM).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_BASE = "https://{subdomain}.amocrm.ru/api/v4"
_OAUTH_URL = "https://{subdomain}.amocrm.ru/oauth2/access_token"
_PAGE_SIZE = 250


class AmocrmClient:
    """Thin async REST v4 клиент. Создавать с already-valid access_token."""

    def __init__(self, subdomain: str, access_token: str) -> None:
        self.subdomain = subdomain
        self._token = access_token
        self._base = _BASE.format(subdomain=subdomain)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    async def _get(self, path: str, params: dict | None = None) -> dict:
        url = self._base + path
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=self._headers(), params=params or {})
            resp.raise_for_status()
            return resp.json()

    async def get_account(self) -> dict:
        return await self._get("/account")

    async def get_pipelines(self) -> list[dict]:
        data = await self._get("/leads/pipelines")
        return data.get("_embedded", {}).get("pipelines", [])

    async def get_users(self) -> list[dict]:
        data = await self._get("/users")
        return data.get("_embedded", {}).get("users", [])

    async def get_leads(
        self,
        date_from: datetime,
        date_to: datetime,
        responsible_user_id: Optional[int] = None,
    ) -> AsyncIterator[dict]:
        """Пагинированный итератор лидов по дате создания (naive UTC → UNIX)."""
        from_ts = int(date_from.replace(tzinfo=timezone.utc).timestamp())
        to_ts = int(date_to.replace(tzinfo=timezone.utc).timestamp())

        page = 1
        while True:
            params = {
                "filter[created_at][from]": from_ts,
                "filter[created_at][to]": to_ts,
                "limit": _PAGE_SIZE,
                "page": page,
                "with": "contacts",
            }
            if responsible_user_id:
                params["filter[responsible_user_id][]"] = responsible_user_id
            try:
                data = await self._get("/leads", params=params)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 204:
                    break  # нет данных
                raise

            leads = data.get("_embedded", {}).get("leads", [])
            if not leads:
                break

            for lead in leads:
                yield lead

            # Проверяем есть ли следующая страница
            links = data.get("_links", {})
            if "next" not in links:
                break
            page += 1


# ---------------------------------------------------------------------------
# OAuth helpers (используются при первичной авторизации и refresh)
# ---------------------------------------------------------------------------

async def exchange_code(code: str) -> dict:
    """Обменивает OAuth-код на токены. Вызывается из /amocrm/oauth/callback."""
    subdomain = settings.amocrm_subdomain
    url = _OAUTH_URL.format(subdomain=subdomain)
    payload = {
        "client_id": settings.amocrm_client_id,
        "client_secret": settings.amocrm_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.amocrm_redirect_uri,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


async def refresh_access_token(refresh_token: str) -> dict:
    """Обновляет access_token по refresh_token."""
    subdomain = settings.amocrm_subdomain
    url = _OAUTH_URL.format(subdomain=subdomain)
    payload = {
        "client_id": settings.amocrm_client_id,
        "client_secret": settings.amocrm_client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "redirect_uri": settings.amocrm_redirect_uri,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


async def get_valid_client() -> Optional[AmocrmClient]:
    """
    Возвращает AmocrmClient с валидным токеном или None если токена нет.
    Автоматически обновляет токен если он истекает через <5 минут.
    """
    from app.db import get_session
    from app.repository import get_amocrm_token, upsert_amocrm_token

    async with get_session() as session:
        token_row = await get_amocrm_token(session)
        if token_row is None:
            logger.warning("AmoCRM token not found. Complete OAuth first: /amocrm/oauth/start")
            return None

        # Проверяем нужно ли обновить
        if token_row.expires_at:
            expires_at_naive = token_row.expires_at
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            if expires_at_naive - now_utc < timedelta(minutes=5):
                logger.info("AmoCRM token expiring soon, refreshing…")
                try:
                    tokens = await refresh_access_token(token_row.refresh_token)
                    expires_in = tokens.get("expires_in", 86400)
                    new_expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=expires_in)
                    await upsert_amocrm_token(
                        session,
                        subdomain=token_row.subdomain,
                        access_token=tokens["access_token"],
                        refresh_token=tokens["refresh_token"],
                        expires_at=new_expires,
                    )
                    return AmocrmClient(token_row.subdomain, tokens["access_token"])
                except Exception as exc:
                    logger.error("Failed to refresh AmoCRM token: %s", exc)
                    return None

        return AmocrmClient(token_row.subdomain, token_row.access_token)
