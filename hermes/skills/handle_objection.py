"""
Skill: handle_objection
Готовый скрипт ответа на возражение клиента.
"""
import json

SCHEMA = {
    "name": "handle_objection",
    "description": (
        "Генерирует скрипт ответа на конкретное возражение клиента. "
        "Используй когда менеджер спрашивает как ответить на 'дорого', 'подумаю', 'не сейчас', "
        "или любое другое возражение клиента."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "objection": {
                "type": "string",
                "description": "Точная фраза возражения клиента или его суть.",
            },
            "product": {
                "type": "string",
                "description": "Товар или категория о которой идёт речь. Опционально.",
            },
            "context": {
                "type": "string",
                "description": "Контекст: на каком этапе, что уже обсуждали. Опционально.",
            },
        },
        "required": ["objection"],
    },
}


async def run(params: dict, context: dict) -> dict:
    llm = context.get("llm")
    if not llm:
        return {"error": "LLM недоступен"}

    objection = params.get("objection", "")
    product = params.get("product", "мебель Neoclassica")
    ctx = params.get("context", "")

    user_msg = f"Возражение клиента: «{objection}»\nТовар: {product}"
    if ctx:
        user_msg += f"\nКонтекст: {ctx}"
    user_msg += (
        "\n\nДай JSON: {\"technique\": \"название техники\", "
        "\"script\": \"точный скрипт ответа\", "
        "\"follow_up\": \"следующий вопрос чтобы продолжить диалог\", "
        "\"avoid\": \"чего не говорить\"}"
    )

    try:
        resp = await llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Ты тренер по продажам мебели класса люкс (Neoclassica, Узбекистан). "
                        "Давай конкретные скрипты, без общих слов. Отвечай JSON без markdown."
                    ),
                },
                {"role": "user", "content": user_msg},
            ],
            temperature=0.5,
            max_tokens=600,
        )
        return json.loads(resp["content"])
    except Exception as exc:
        return {"error": str(exc)}
