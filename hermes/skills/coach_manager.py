"""
Skill: coach_manager
Анализ показателей конкретного менеджера и персональные рекомендации РОПа.
"""
import json

SCHEMA = {
    "name": "coach_manager",
    "description": (
        "Анализирует активные сделки менеджера и даёт конкретный коучинг: "
        "что делает хорошо, где просадка, что исправить. "
        "Используй когда спрашивают как дела у конкретного менеджера, кому нужна помощь, разбор по сотруднику."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "manager_name": {
                "type": "string",
                "description": "Имя менеджера (для контекста в ответе).",
            },
            "amocrm_user_id": {
                "type": "integer",
                "description": "AmoCRM user_id менеджера. Если не указан — берётся из контекста запроса.",
            },
        },
        "required": [],
    },
}


async def run(params: dict, context: dict) -> dict:
    from dataclasses import asdict

    from hermes.audit import run_audit

    llm = context.get("llm")
    if not llm:
        return {"error": "LLM недоступен"}

    amocrm_user_id = params.get("amocrm_user_id") or context.get("amocrm_user_id")
    manager_name = params.get("manager_name") or "Менеджер"

    if not amocrm_user_id:
        return {"error": "Не указан amocrm_user_id менеджера"}

    try:
        deals = await run_audit(amocrm_user_id, tg_user_id=0, with_suggestions=False)
    except Exception as exc:
        return {"error": f"Не удалось получить данные: {exc}"}

    if not deals:
        return {"manager": manager_name, "message": "Нет активных сделок"}

    hot = [d for d in deals if d.heat == "hot"]
    warm = [d for d in deals if d.heat == "warm"]
    cold = [d for d in deals if d.heat == "cold"]
    avg_inactive = round(sum(d.days_inactive for d in deals) / len(deals), 1)
    total_value = sum(d.lead_price for d in deals)

    summary = (
        f"Менеджер: {manager_name}\n"
        f"Активных сделок: {len(deals)} (сумма: {total_value:,.0f})\n"
        f"Горячих: {len(hot)}, Тёплых: {len(warm)}, Холодных: {len(cold)}\n"
        f"Среднее время без активности: {avg_inactive} дн.\n"
        f"Топ-5 сделок:\n"
    )
    for d in deals[:5]:
        summary += f"  - {d.lead_name} | {d.heat} | {d.days_inactive}дн | {d.lead_price:,.0f}\n"

    prompt = (
        f"{summary}\n"
        "Ты РОП. Дай коучинг этому менеджеру. Верни JSON:\n"
        "{\"strengths\": [\"что делает хорошо\"], "
        "\"issues\": [\"конкретные проблемы с цифрами\"], "
        "\"priority_actions\": [\"что сделать сегодня\"], "
        "\"coaching_message\": \"мотивирующее сообщение менеджеру (1-2 предложения)\"}"
    )

    try:
        resp = await llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Ты жёсткий но справедливый РОП мебельного бизнеса. "
                        "Давай конкретный коучинг с цифрами, без воды. Отвечай JSON без markdown."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=600,
        )
        result = json.loads(resp["content"])
        result["manager"] = manager_name
        result["deals_summary"] = {
            "total": len(deals), "hot": len(hot), "warm": len(warm),
            "cold": len(cold), "total_value": total_value, "avg_inactive_days": avg_inactive,
        }
        return result
    except Exception as exc:
        return {"error": str(exc)}
