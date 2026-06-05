"""
AmoCRM аналитика: метрики лидов за период.

Лид считается НЕобработанным если его status_id входит в группу
начальных статусов воронки (все статусы с минимальным sort-значением,
т.е. «Неразобранное» + «Новый лид / Yangi lid» и т.п.).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

from app.amocrm.client import AmocrmClient, get_valid_client

logger = logging.getLogger(__name__)

# pipeline_id → set of status_id которые считаются "необработанными"
_unprocessed_cache: dict[int, set[int]] = {}

# user_id → имя сотрудника
_users_cache: dict[int, str] = {}


async def _load_unprocessed_statuses(client: AmocrmClient) -> None:
    """Загружает все воронки и определяет начальные (необработанные) статусы."""
    try:
        pipelines = await client.get_pipelines()
        for pl in pipelines:
            pid = pl.get("id")
            statuses = pl.get("_embedded", {}).get("statuses", [])
            if not statuses or not pid:
                continue

            # Исключаем системные статусы AmoCRM (id 142 = Успешно, 143 = Закрыто)
            real_statuses = [s for s in statuses if s.get("id") not in (142, 143)]
            if not real_statuses:
                continue

            # Все статусы с минимальным sort — «необработанные»
            min_sort = min(s.get("sort", 9999) for s in real_statuses)
            unprocessed = {
                s["id"] for s in real_statuses
                if s.get("sort", 9999) <= min_sort and s.get("id")
            }
            _unprocessed_cache[pid] = unprocessed
            logger.debug(
                "Pipeline %s '%s': unprocessed statuses = %s",
                pid, pl.get("name"), unprocessed,
            )
    except Exception as exc:
        logger.warning("Could not load pipeline statuses: %s", exc)


def _is_unprocessed(pipeline_id: Optional[int], status_id: Optional[int]) -> bool:
    """Возвращает True если лид в начальном (необработанном) статусе."""
    if not pipeline_id or not status_id:
        return True  # нет данных — считаем необработанным
    unprocessed = _unprocessed_cache.get(pipeline_id)
    if unprocessed is None:
        return True  # воронка не найдена — считаем необработанным
    return status_id in unprocessed


async def _load_users(client: AmocrmClient) -> None:
    """Загружает список сотрудников AmoCRM в кэш."""
    try:
        users = await client.get_users()
        for u in users:
            uid = u.get("id")
            name = u.get("name") or u.get("email") or f"User {uid}"
            if uid:
                _users_cache[uid] = name
        logger.debug("Loaded %d AmoCRM users", len(_users_cache))
    except Exception as exc:
        logger.warning("Could not load AmoCRM users: %s", exc)


async def get_lead_metrics(
    from_utc: datetime,
    to_utc: datetime,
    tz_name: str = "Asia/Tashkent",
    responsible_user_id: Optional[int] = None,   # фильтр для продавца
) -> dict:
    """
    Возвращает метрики лидов AmoCRM за период.

    {
      "total_leads": int,
      "processed_leads": int,
      "unprocessed_leads": int,
      "conversion_rate": float,
      "hourly": {hour: count},
      "error": str | None,
    }
    """
    from datetime import timezone
    from zoneinfo import ZoneInfo

    from app.periods import SHIFTS, is_in_shift, is_work_hour

    tz = ZoneInfo(tz_name)
    client = await get_valid_client()
    if client is None:
        return {
            "total_leads": 0, "processed_leads": 0, "unprocessed_leads": 0,
            "conversion_rate": 0.0, "hourly": {}, "work_hours": 0, "non_work_hours": 0,
            "shifts": {}, "error": "AmoCRM не авторизован"
        }

    # Загружаем статусы воронок (один раз, потом кэш)
    if not _unprocessed_cache:
        await _load_unprocessed_statuses(client)

    total = 0
    unprocessed = 0
    hourly: dict[tuple[str, int], int] = defaultdict(int)
    work_hours = 0
    non_work_hours = 0
    shifts: dict[str, int] = {name: 0 for name, _, _ in SHIFTS}
    seen_ids: set[int] = set()

    try:
        async for lead in client.get_leads(
            from_utc, to_utc, responsible_user_id=responsible_user_id
        ):
            lead_id = lead.get("id")
            if lead_id and lead_id in seen_ids:
                continue
            if lead_id:
                seen_ids.add(lead_id)
            total += 1

            pipeline_id = lead.get("pipeline_id")
            status_id = lead.get("status_id")

            if _is_unprocessed(pipeline_id, status_id):
                unprocessed += 1

            # Почасовое распределение + рабочее время + смены
            created_at_ts = lead.get("created_at")
            if created_at_ts:
                try:
                    local_dt = datetime.fromtimestamp(
                        created_at_ts, tz=timezone.utc
                    ).astimezone(tz)
                    h = local_dt.hour
                    date_key = local_dt.strftime("%d.%m.%Y")
                    hourly[(date_key, h)] += 1
                    if is_work_hour(h):
                        work_hours += 1
                    else:
                        non_work_hours += 1
                    for name, s_start, s_end in SHIFTS:
                        if is_in_shift(h, s_start, s_end):
                            shifts[name] += 1
                except Exception:
                    pass

    except Exception as exc:
        logger.error("AmoCRM leads fetch error: %s", exc)
        return {
            "total_leads": 0, "processed_leads": 0, "unprocessed_leads": 0,
            "conversion_rate": 0.0, "hourly": {}, "error": str(exc)
        }

    processed = total - unprocessed
    conv_rate = round(processed / total * 100, 1) if total > 0 else 0.0

    return {
        "total_leads": total,
        "processed_leads": processed,
        "unprocessed_leads": unprocessed,
        "conversion_rate": conv_rate,
        "hourly": dict(hourly),
        "work_hours": work_hours,
        "non_work_hours": non_work_hours,
        "shifts": shifts,
        "error": None,
    }


async def get_lead_metrics_by_users(
    from_utc: datetime,
    to_utc: datetime,
    tz_name: str = "Asia/Tashkent",
) -> dict:
    """
    Метрики лидов AmoCRM за период, сгруппированные по ответственному сотруднику.

    {
      "users": {
          user_id: {
              "name": str,
              "total": int,
              "processed": int,
              "unprocessed": int,
              "conversion_rate": float,
          }
      },
      "total_leads": int,
      "error": str | None,
    }
    """
    client = await get_valid_client()
    if client is None:
        return {"users": {}, "total_leads": 0, "error": "AmoCRM не авторизован"}

    if not _unprocessed_cache:
        await _load_unprocessed_statuses(client)

    if not _users_cache:
        await _load_users(client)

    # user_id → {"name", "total", "unprocessed"}
    per_user: dict[int, dict] = {}
    total_all = 0
    seen_ids: set[int] = set()

    try:
        async for lead in client.get_leads(from_utc, to_utc):
            lead_id = lead.get("id")
            if lead_id and lead_id in seen_ids:
                continue
            if lead_id:
                seen_ids.add(lead_id)
            total_all += 1

            uid = lead.get("responsible_user_id") or 0
            if uid not in per_user:
                per_user[uid] = {
                    "name": _users_cache.get(uid, f"ID {uid}"),
                    "total": 0,
                    "unprocessed": 0,
                }
            per_user[uid]["total"] += 1

            pipeline_id = lead.get("pipeline_id")
            status_id = lead.get("status_id")
            if _is_unprocessed(pipeline_id, status_id):
                per_user[uid]["unprocessed"] += 1

    except Exception as exc:
        logger.error("AmoCRM leads by users fetch error: %s", exc)
        return {"users": {}, "total_leads": 0, "error": str(exc)}

    # Вычисляем processed и conversion_rate
    result_users = {}
    for uid, data in per_user.items():
        processed = data["total"] - data["unprocessed"]
        conv = round(processed / data["total"] * 100, 1) if data["total"] > 0 else 0.0
        result_users[uid] = {
            "name": data["name"],
            "total": data["total"],
            "processed": processed,
            "unprocessed": data["unprocessed"],
            "conversion_rate": conv,
        }

    return {
        "users": result_users,
        "total_leads": total_all,
        "error": None,
    }
