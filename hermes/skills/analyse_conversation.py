import json

SCHEMA = {
    "name": "analyse_conversation",
    "description": "Анализирует историю переписки и заметок по сделке",
    "parameters": {
        "type": "object",
        "properties": {
            "lead_name": {"type": "string"},
            "notes_text": {"type": "string"},
        },
        "required": ["lead_name", "notes_text"],
    },
}


async def run(params: dict, context: dict) -> dict:
    notes = (params.get("notes_text") or "").strip()
    if not notes:
        return {"sentiment": "neutral", "summary": "Нет истории переписки", "client_objections": []}

    llm = context["llm"]
    result = await llm.chat(
        messages=[
            {"role": "system", "content": "Ты анализируешь историю продаж. Отвечай ТОЛЬКО в JSON без markdown."},
            {"role": "user", "content": f"Сделка: {params['lead_name']}\n\nЗаметки:\n{notes[:3000]}\n\nОтветь JSON: {{\"sentiment\": \"positive|neutral|negative\", \"summary\": \"резюме\", \"client_objections\": [\"...\"]}}"},
        ],
        temperature=0.1,
    )
    try:
        return json.loads(result["content"])
    except Exception:
        return {"sentiment": "neutral", "summary": (result.get("content") or "")[:200], "client_objections": []}
