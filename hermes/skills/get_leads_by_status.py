"""
Skill: get_leads_by_status
Лиды в конкретном статусе/этапе воронки — с именами и ссылками.
"""
SCHEMA = {
    "name": "get_leads_by_status",
    "description": (
        "Возвращает лиды которые находятся в конкретном этапе/статусе воронки. "
        "Используй когда спрашивают: сколько дошло до этапа X, "
        "покажи сделки в статусе 'Dostavka chiqarish kerak', "
        "сколько на этапе доставки/оплаты/встречи и т.д."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status_name": {
                "type": "string",
                "description": "Название этапа/статуса (часть названия, поиск без учёта регистра).",
            },
            "period": {
                "type": "string",
                "enum": ["today", "yesterday", "this_week", "last_week", "this_month"],
                "description": "Период создания лида. По умолчанию — this_week.",
            },
            "limit": {
                "type": "integer",
                "description": "Максимум лидов. По умолчанию 30.",
            },
        },
        "required": ["status_name"],
    },
}


async def run(params: dict, context: dict) -> dict:
    from app.amocrm.client import get_valid_client
    from app.config import settings
    from app.periods import PERIOD_FUNCS, PERIOD_LABELS

    status_name = params.get("status_name", "").lower().strip()
    key = params.get("period", "this_week")
    if key not in PERIOD_FUNCS:
        key = "this_week"
    limit = min(int(params.get("limit") or 30), 100)

    from_utc, to_utc = PERIOD_FUNCS[key]()

    client = await get_valid_client()
    if client is None:
        return {"error": "AmoCRM не авторизован"}

    # Загружаем воронки и строим маппинг status_id → status_name + pipeline_name
    try:
        pipelines = await client.get_pipelines()
    except Exception as exc:
        return {"error": f"Не удалось загрузить воронки: {exc}"}

    status_map: dict[int, dict] = {}
    for pl in pipelines:
        for s in pl.get("_embedded", {}).get("statuses", []):
            status_map[s["id"]] = {
                "status_name": s.get("name", ""),
                "pipeline_name": pl.get("name", ""),
                "pipeline_id": pl.get("id"),
            }

    # Найдём все status_id подходящие под запрос
    matched_ids = {
        sid for sid, info in status_map.items()
        if status_name in info["status_name"].lower()
    }

    if not matched_ids:
        known = sorted({info["status_name"] for info in status_map.values()})
        return {
            "error": f"Этап '{status_name}' не найден.",
            "available_statuses": known[:20],
        }

    leads = []
    async for lead in client.get_leads(from_utc, to_utc):
        if lead.get("status_id") not in matched_ids:
            continue
        lead_id = lead.get("id")
        sid = lead.get("status_id")
        info = status_map.get(sid, {})
        leads.append({
            "id": lead_id,
            "name": lead.get("name") or f"Сделка #{lead_id}",
            "price": lead.get("price") or 0,
            "responsible": (lead.get("responsible_user") or {}).get("name") or "—",
            "status": info.get("status_name", ""),
            "pipeline": info.get("pipeline_name", ""),
            "url": f"https://{settings.amocrm_subdomain}.amocrm.ru/leads/detail/{lead_id}",
        })
        if len(leads) >= limit:
            break

    total_price = sum(l["price"] for l in leads)
    return {
        "period": PERIOD_LABELS.get(key, key),
        "status_searched": status_name,
        "count": len(leads),
        "total_price": total_price,
        "leads": leads,
    }
