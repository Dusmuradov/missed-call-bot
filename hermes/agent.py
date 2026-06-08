from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Ты Hermes — AI руководитель отдела продаж (РОП) мебельной компании Neoclassica (Узбекистан).

Твоя задача — помогать команде продавать больше и эффективнее. У тебя есть инструменты (skills) для получения реальных данных: воронка AmoCRM, звонки Utel, склад и продажи BILLZ.

Как ты работаешь:
- Когда нужны цифры — используй инструменты, не придумывай
- Отвечай конкретно: цифры, скрипты, следующие шаги
- Если менеджер описывает ситуацию с клиентом — квалифицируй лид и дай скрипт
- Если спрашивают о возражении — дай готовый ответ
- Если спрашивают о команде или показателях — запроси данные и разбери их
- Никогда не говори «это зависит» без конкретного ответа следом

Стиль: прямой, деловой, без воды. Как опытный РОП на планёрке.
Язык: русский."""


async def ask(tg_user_id: int, user_text: str, amocrm_user_id: int | None = None) -> str:
    """
    Основная точка входа: обрабатывает сообщение менеджера через tool-loop.
    Возвращает текстовый ответ агента.
    """
    from hermes.context import get_history, save_message, trim_history
    from hermes.llm import get_llm_client
    from hermes.skills.loader import call_skill, load_skills

    llm = get_llm_client()
    tools = load_skills()
    history = await get_history(tg_user_id, limit=20)

    context = {"llm": llm, "amocrm_user_id": amocrm_user_id, "tg_user_id": tg_user_id}

    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": user_text},
    ]

    answer = ""
    for iteration in range(5):
        response = await llm.chat(messages, tools=tools if tools else None, temperature=0.3)

        if response["tool_calls"]:
            # Добавляем ответ ассистента с tool_calls в историю
            messages.append({
                "role": "assistant",
                "content": response["content"],
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": tc["function"],
                    }
                    for tc in response["tool_calls"]
                ],
            })

            # Выполняем каждый tool call
            for tc in response["tool_calls"]:
                skill_name = tc["function"]["name"]
                try:
                    params = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    params = {}

                logger.debug("Agent: calling skill %s (iter=%d)", skill_name, iteration)
                result = await call_skill(skill_name, params, context)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })

        else:
            # Финальный текстовый ответ
            answer = (response["content"] or "").strip()
            break
    else:
        answer = "Не удалось получить ответ — превышено количество шагов."

    if answer:
        await save_message(tg_user_id, "user", user_text)
        await save_message(tg_user_id, "assistant", answer)
        await trim_history(tg_user_id, keep=50)

    return answer or "Нет ответа от агента."
