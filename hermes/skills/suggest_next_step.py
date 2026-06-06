import json

SCHEMA = {
    "name": "suggest_next_step",
    "description": "Генерирует конкретный следующий шаг и скрипт для работы с клиентом",
    "parameters": {
        "type": "object",
        "properties": {
            "lead_name": {"type": "string"},
            "heat": {"type": "string"},
            "sentiment": {"type": "string"},
            "status_name": {"type": "string"},
            "client_objections": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["lead_name", "heat", "status_name"],
    },
}


async def run(params: dict, context: dict) -> dict:
    llm = context["llm"]
    objections = params.get("client_objections") or []
    obj_text = "; ".join(objections) if objections else "нет"

    result = await llm.chat(
        messages=[
            {"role": "system", "content": "Ты опытный менеджер по продажам. Давай КОНКРЕТНЫЕ скрипты, не общие советы. Отвечай JSON."},
            {"role": "user", "content": f"Сделка: {params['lead_name']}\nТемпература: {params['heat']}\nНастрой: {params.get('sentiment','neutral')}\nСтатус: {params['status_name']}\nВозражения: {obj_text}\n\nДай JSON: {{\"action\": \"звонок|письмо|встреча|КП\", \"script\": \"что сказать\", \"urgency\": \"сегодня|завтра|на этой неделе\"}}"},
        ],
        temperature=0.7,
        max_tokens=500,
    )
    try:
        return json.loads(result["content"])
    except Exception:
        return {"action": "звонок", "script": result.get("content") or "", "urgency": "сегодня"}
