"""
AmoCRM REST v4 клиент.
Авторизация только через AMOCRM_LONG_LIVED_TOKEN из .env / Railway Variables.
"""
from __future__ import annotations

import logging
from hashlib import sha1
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional
from zoneinfo import ZoneInfo

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

    async def _post(self, path: str, payload: Any) -> dict:
        url = self._base + path
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(url, headers=self._headers(), json=payload)
                resp.raise_for_status()
                if resp.status_code == 204 or not resp.content:
                    return {}
                return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                logger.error(
                    "AmoCRM %d при POST %s — проверьте права AMOCRM_LONG_LIVED_TOKEN",
                    exc.response.status_code,
                    path,
                )
                await _notify_token_invalid(exc.response.status_code)
            raise

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

    async def create_task(
        self,
        lead_id: int,
        text: str,
        complete_till_ts: int,
        responsible_user_id: int,
        task_type_id: int = 1,
    ) -> dict:
        """
        Создаёт задачу по сделке с защитой от дублей.

        Если уже есть открытая задача с тем же текстом на тот же локальный день,
        POST в AmoCRM не выполняется и возвращается существующая задача.
        """
        text = " ".join((text or "").split())
        if not lead_id:
            raise ValueError("lead_id is required")
        if not text:
            raise ValueError("text is required")
        if not complete_till_ts:
            raise ValueError("complete_till_ts is required")
        if not responsible_user_id:
            raise ValueError("responsible_user_id is required")

        existing_tasks = await self.get_lead_tasks(lead_id)
        duplicate = _find_duplicate_task(existing_tasks, text, complete_till_ts)
        if duplicate:
            return {"created": False, "duplicate": True, "task": duplicate}

        request_id = _task_request_id(lead_id, text, complete_till_ts)
        payload = [{
            "task_type_id": int(task_type_id),
            "text": text,
            "complete_till": int(complete_till_ts),
            "entity_id": int(lead_id),
            "entity_type": "leads",
            "responsible_user_id": int(responsible_user_id),
            "request_id": request_id,
        }]
        data = await self._post("/tasks", payload)
        created = (data.get("_embedded") or {}).get("tasks") or []
        task = created[0] if created else data
        return {"created": True, "duplicate": False, "task": task, "raw": data}


def _task_request_id(lead_id: int, text: str, complete_till_ts: int) -> str:
    digest = sha1(f"{lead_id}:{text}:{complete_till_ts}".encode("utf-8")).hexdigest()[:12]
    return f"rop-{lead_id}-{digest}"


def _normalise_task_text(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


def _task_local_date(complete_till_ts: Any):
    if not complete_till_ts:
        return None
    try:
        ts = int(complete_till_ts)
        if ts > 10_000_000_000:
            ts //= 1000
        tz = ZoneInfo(settings.timezone)
        return datetime.fromtimestamp(ts, timezone.utc).astimezone(tz).date()
    except Exception:
        return None


def _find_duplicate_task(tasks: list[dict], text: str, complete_till_ts: int) -> dict | None:
    expected_text = _normalise_task_text(text)
    expected_date = _task_local_date(complete_till_ts)
    for task in tasks:
        if task.get("is_completed"):
            continue
        if _normalise_task_text(task.get("text")) != expected_text:
            continue
        if _task_local_date(task.get("complete_till")) == expected_date:
            return task
    return None


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
