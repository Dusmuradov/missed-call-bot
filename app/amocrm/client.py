"""
AmoCRM REST v4 клиент.
Авторизация только через AMOCRM_LONG_LIVED_TOKEN из .env / Railway Variables.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_BASE = "https://{subdomain}.amocrm.ru/api/v4"
_PAGE_SIZE = 250


class AmocrmClient:
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
            if resp.status_code == 204 or not resp.content:
                return {}
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
        from_ts = int(date_from.replace(tzinfo=timezone.utc).timestamp())
        to_ts = int(date_to.replace(tzinfo=timezone.utc).timestamp())

        page = 1
        while True:
            params = {
                "filter[created_at][from]": from_ts,
                "filter[created_at][to]": to_ts,
                "limit": _PAGE_SIZE,
                "page": page,
            }
            if responsible_user_id:
                params["filter[responsible_user_id][]"] = responsible_user_id
            try:
                data = await self._get("/leads", params=params)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 204:
                    break
                if exc.response.status_code in (401, 403):
                    logger.error(
                        "AmoCRM %d при запросе лидов — обновите AMOCRM_LONG_LIVED_TOKEN",
                        exc.response.status_code,
                    )
                    await _notify_token_invalid(exc.response.status_code)
                    return
                raise

            leads = data.get("_embedded", {}).get("leads", [])
            if not leads:
                break
            for lead in leads:
                yield lead

            if "next" not in data.get("_links", {}):
                break
            page += 1

    async def get_active_leads(self, responsible_user_id: int) -> AsyncIterator[dict]:
        page = 1
        while True:
            params = {
                "filter[responsible_user_id][]": responsible_user_id,
                "limit": _PAGE_SIZE,
                "page": page,
            }
            try:
                data = await self._get("/leads", params=params)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 204:
                    break
                if exc.response.status_code in (401, 403):
                    logger.error(
                        "AmoCRM %d при запросе активных лидов — обновите AMOCRM_LONG_LIVED_TOKEN",
                        exc.response.status_code,
                    )
                    await _notify_token_invalid(exc.response.status_code)
                    return
                raise

            leads = data.get("_embedded", {}).get("leads", [])
            if not leads:
                break
            for lead in leads:
                if lead.get("status_id") in (142, 143):
                    continue
                yield lead

            if "next" not in data.get("_links", {}):
                break
            page += 1

    async def get_lead_notes(self, lead_id: int) -> list[dict]:
        try:
            data = await self._get(
                f"/leads/{lead_id}/notes",
                params={"limit": 50, "order[id]": "desc"},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise
        return data.get("_embedded", {}).get("notes", [])

    async def get_lead_tasks(self, lead_id: int) -> list[dict]:
        try:
            data = await self._get(
                "/tasks",
                params={
                    "filter[entity_id]": lead_id,
                    "filter[entity_type]": "leads",
                    "filter[is_completed]": 0,
                    "limit": 50,
                },
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 204):
                return []
            raise
        return data.get("_embedded", {}).get("tasks", [])


async def _notify_token_invalid(status_code: int) -> None:
    try:
        from app.telegram import send_to_user
        if settings.admin_user_id:
            msg = (
                f"⚠️ <b>AmoCRM: токен недействителен (HTTP {status_code})</b>\n\n"
                "Обновите переменную <code>AMOCRM_LONG_LIVED_TOKEN</code> "
                "в Railway Variables и перезапустите сервис."
            )
            await send_to_user(settings.admin_user_id, msg)
    except Exception:
        pass


async def get_valid_client() -> Optional[AmocrmClient]:
    """Возвращает AmocrmClient с long-lived токеном или None если токен не задан."""
    if not settings.amocrm_long_lived_token:
        logger.warning("AMOCRM_LONG_LIVED_TOKEN не задан — AmoCRM недоступен")
        return None
    return AmocrmClient(settings.amocrm_subdomain, settings.amocrm_long_lived_token)
