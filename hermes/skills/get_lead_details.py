"""
Skill: get_lead_details
Детали конкретной сделки AmoCRM + советы: заметки, задачи, контакт, следующий шаг.
"""
import re

SCHEMA = {
    "name": "get_lead_details",
    "description": (
        "Возвращает полную информацию по конкретной сделке AmoCRM: имя, цена, статус, "
        "ответственный, заметки, открытые задачи, контакт. "
        "Используй когда пользователь прислал ссылку на сделку вида "
        "https://*.amocrm.ru/leads/detail/12345 или называет конкретный ID сделки, "
        "а также когда просят проанализировать/дать совет по конкретной сделке."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "lead_id": {
                "type": "integer",
                "description": "ID сделки AmoCRM. Извлеки из URL /leads/detail/{id} или из текста.",
            },
        },
        "required": ["lead_id"],
    },
}


async def run(params: dict, context: dict) -> dict:
    from app.amocrm.client import get_valid_client
    from app.config import settings

    lead_id = int(params.get("lead_id", 0))
    if not lead_id:
        return {"error": "lead_id не передан"}

    client = await get_valid_client()
    if client is None:
        return {"error": "AmoCRM не авторизован"}

    try:
        data = await client._get(f"/leads/{lead_id}", params={"with": "contacts,notes,tasks"})
    except Exception as exc:
        return {"error": str(exc)}

    if not data or not data.get("id"):
        return {"error": f"Сделка #{lead_id} не найдена"}

    lead = data

    # Контакты
    contacts_raw = (lead.get("_embedded") or {}).get("contacts") or []
    contacts = [{"name": c.get("name"), "id": c.get("id")} for c in contacts_raw]

    pipeline_id = lead.get("pipeline_id")
    status_id = lead.get("status_id")
    pipeline_name = f"Pipeline {pipeline_id}"
    try:
        from app.amocrm.reports import _pipeline_names_cache, _load_unprocessed_statuses
        if pipeline_id not in _pipeline_names_cache:
            await _load_unprocessed_statuses(client)
        pipeline_name = _pipeline_names_cache.get(pipeline_id, pipeline_name)
    except Exception:
        pass

    # Заметки
    notes = []
    try:
        notes_raw = await client.get_lead_notes(lead_id)
        for n in notes_raw[:5]:
            params_n = n.get("params") or {}
            text = params_n.get("text") or params_n.get("note") or ""
            if text:
                notes.append({"type": n.get("note_type"), "text": text[:300]})
    except Exception:
        pass

    # Задачи
    tasks = []
    try:
        tasks_raw = await client.get_lead_tasks(lead_id)
        for t in tasks_raw:
            tasks.append({
                "text": t.get("text") or "",
                "due_at": t.get("complete_till"),
            })
    except Exception:
        pass

    return {
        "id": lead_id,
        "name": lead.get("name") or f"Сделка #{lead_id}",
        "deal_estimate_uzs": lead.get("price") or 0,  # оценка менеджера, не фактическая продажа
        "status_id": status_id,
        "pipeline": pipeline_name,
        "responsible_id": lead.get("responsible_user_id"),
        "created_at": lead.get("created_at"),
        "updated_at": lead.get("updated_at"),
        "contacts": contacts,
        "notes": notes,
        "open_tasks": tasks,
        "url": f"https://{settings.amocrm_subdomain}.amocrm.ru/leads/detail/{lead_id}",
    }
