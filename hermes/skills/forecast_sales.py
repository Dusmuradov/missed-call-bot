"""
Skill: forecast_sales
Прогноз продаж на основе последних 7 дней из BillzSnapshot + AI.
"""
import json

SCHEMA = {
    "name": "forecast_sales",
    "description": (
        "Прогноз продаж на ближайшие дни на основе текущего тренда. "
        "Используй когда спрашивают план, прогноз, сколько продадим, выйдем ли на цель."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "days_ahead": {
                "type": "integer",
                "description": "На сколько дней вперёд прогнозировать. По умолчанию 7.",
            },
            "target_revenue": {
                "type": "number",
                "description": "Целевая выручка (если есть план). Опционально.",
            },
        },
        "required": [],
    },
}


async def run(params: dict, context: dict) -> dict:
    from datetime import date, timedelta

    from sqlalchemy import select

    from app.db import get_session
    from app.models import BillzSnapshot

    days_ahead = min(int(params.get("days_ahead") or 7), 30)
    target = params.get("target_revenue")

    # Загружаем последние 14 дней снапшотов
    cutoff = str(date.today() - timedelta(days=14))
    try:
        async with get_session() as session:
            result = await session.execute(
                select(BillzSnapshot)
                .where(BillzSnapshot.snapshot_date >= cutoff)
                .order_by(BillzSnapshot.snapshot_date.asc())
            )
            snapshots = result.scalars().all()
    except Exception as exc:
        return {"error": f"Не удалось загрузить историю: {exc}"}

    if not snapshots:
        return {"error": "Нет исторических данных для прогноза (нужно минимум несколько дней работы)"}

    history = [
        {"date": s.snapshot_date, "revenue": s.revenue, "orders": s.orders, "aov": s.aov}
        for s in snapshots
    ]

    llm = context.get("llm")
    if not llm:
        return {"error": "LLM недоступен"}

    prompt = (
        f"История продаж (последние дни):\n{json.dumps(history, ensure_ascii=False)}\n\n"
        f"Задача: спрогнозируй выручку на следующие {days_ahead} дней.\n"
    )
    if target:
        prompt += f"Целевая выручка: {target:,.0f} сум\n"
    prompt += (
        "Верни JSON:\n"
        "{\"trend\": \"растущий|стабильный|падающий\", "
        "\"avg_daily_revenue\": число, "
        "\"forecast_total\": число за период, "
        "\"forecast_daily\": [число, ...], "
        "\"will_hit_target\": true/false или null если цели нет, "
        "\"insight\": \"1-2 предложения о тренде и рекомендации\"}"
    )

    try:
        resp = await llm.chat(
            [
                {"role": "system", "content": "Ты финансовый аналитик. Отвечай ТОЛЬКО JSON без markdown."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=600,
        )
        return json.loads(resp["content"])
    except Exception as exc:
        return {"error": f"AI прогноз недоступен: {exc}"}
