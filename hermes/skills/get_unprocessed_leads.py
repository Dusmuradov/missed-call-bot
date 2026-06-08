"""
Skill: get_unprocessed_leads
Список необработанных лидов с именами, ценой и ссылками.
"""
SCHEMA = {
    "name": "get_unprocessed_leads",
    "description": (
        "Возвращает список необработанных лидов с именами, ответственными и ссылками на сделки. "
        "Используй когда спрашивают: какие лиды не обработаны, дай ссылки на лиды, "
        "кто висит без обработки, покажи конкретные необработанные сделки."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["today", "yesterday", "this_week", "last_week", "this_month", "last_month", "this_quarter", "last_quarter", "this_year"],
                "description": "Период создания лида. По умолчанию — today.",
            },
            "limit": {
                "type": "integer",
                "description": "Максимум лидов в ответе. По умолчанию 20.",
            },
        },
        "required": [],
    },
}


async def run(params: dict, context: dict) -> dict:
    from app.amocrm.client import get_valid_client
    from app.amocrm.reports import _load_unprocessed_statuses, _unprocessed_cache
    from app.config import settings
    from app.periods import PERIOD_FUNCS, PERIOD_LABELS

    key = params.get("period", "today")
    if key not in PERIOD_FUNCS:
        key = "today"
    limit = min(int(params.get("limit") or 20), 50)

    from_utc, to_utc = PERIOD_FUNCS[key]()

    client = await get_valid_client()
    if client is None:
        return {"error": "AmoCRM не авторизован"}

    if not _unprocessed_cache:
        await _load_unprocessed_statuses(client)

    leads = []
    async for lead in client.get_leads(from_utc, to_utc):
        pid = lead.get("pipeline_id")
        sid = lead.get("status_id")
        unproc_ids = _unprocessed_cache.get(pid, set())
        if sid not in unproc_ids:
            continue

        lead_id = lead.get("id")
        leads.append({
            "id": lead_id,
            "name": lead.get("name") or f"Сделка #{lead_id}",
            "price": lead.get("price") or 0,
            "responsible": (lead.get("responsible_user") or {}).get("name") or "—",
            "status_id": sid,
            "url": f"https://{settings.amocrm_subdomain}.amocrm.ru/leads/detail/{lead_id}",
        })
        if len(leads) >= limit:
            break

    return {
        "period": PERIOD_LABELS.get(key, key),
        "count": len(leads),
        "leads": leads,
    }


