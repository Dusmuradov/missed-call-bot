"""
Skill: qualify_lead
Квалификация лида по описанию разговора — BANT-оценка.
"""
import json

SCHEMA = {
    "name": "qualify_lead",
    "description": (
        "Квалифицирует лид по описанию: оценивает бюджет, потребность, полномочия, сроки (BANT). "
        "Используй когда менеджер описывает клиента и хочет понять насколько он перспективен."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Описание клиента: что спрашивал, что говорил, бюджет, сроки.",
            },
            "product_interest": {
                "type": "string",
                "description": "Каким товаром или категорией интересуется. Опционально.",
            },
        },
        "required": ["description"],
    },
}


async def run(params: dict, context: dict) -> dict:
    llm = context.get("llm")
    if not llm:
        return {"error": "LLM недоступен"}

    desc = params.get("description", "")
    product = params.get("product_interest", "")

    user_msg = f"Описание клиента: {desc}"
    if product:
        user_msg += f"\nИнтерес к: {product}"
    user_msg += (
        "\n\nКвалифицируй лид. Верни JSON:\n"
        "{\"score\": 1-10, "
        "\"grade\": \"горячий|тёплый|холодный\", "
        "\"budget\": \"высокий|средний|низкий|неизвестен\", "
        "\"need\": \"чёткая|размытая|отсутствует\", "
        "\"authority\": \"ЛПР|влияет|не ЛПР|неизвестно\", "
        "\"timeline\": \"срочно|в ближайший месяц|не определено\", "
        "\"next_action\": \"что сделать прямо сейчас\", "
        "\"red_flags\": [\"риски\"]}"
    )

    try:
        resp = await llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Ты опытный РОП мебельного бизнеса люкс-сегмента (Neoclassica, Узбекистан). "
                        "Квалифицируй лиды жёстко и реалистично. Отвечай JSON без markdown."
                    ),
                },
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=500,
        )
        return json.loads(resp["content"])
    except Exception as exc:
        return {"error": str(exc)}
