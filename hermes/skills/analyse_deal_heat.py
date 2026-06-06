SCHEMA = {
    "name": "analyse_deal_heat",
    "description": "Оценивает приоритет сделки (hot/warm/cold) на основе активности",
    "parameters": {
        "type": "object",
        "properties": {
            "lead_name": {"type": "string"},
            "lead_id": {"type": "integer"},
            "days_inactive": {"type": "integer"},
            "lead_price": {"type": "number"},
            "status_name": {"type": "string"},
            "last_note": {"type": "string"},
            "open_tasks_count": {"type": "integer"},
        },
        "required": ["lead_name", "lead_id", "days_inactive"],
    },
}


async def run(params: dict, context: dict) -> dict:
    days = params.get("days_inactive", 999)
    price = params.get("lead_price", 0) or 0
    tasks = params.get("open_tasks_count", 0) or 0

    if (days <= 1 and tasks > 0) or days <= 3 or price >= 500_000:
        heat = "hot"
        score = max(7, 10 - days)
    elif days <= 7:
        heat = "warm"
        score = max(4, 7 - days // 2)
    elif days > 30:
        heat = "cold"
        score = max(1, 4 - days // 10)
    else:
        heat = "warm"
        score = max(4, 7 - days // 2)

    reason_parts = [f"{days} дн. без активности"]
    if price:
        reason_parts.append(f"сумма {price:,.0f}₽")
    if tasks:
        reason_parts.append(f"задач: {tasks}")

    return {
        "heat": heat,
        "score": int(min(score, 10)),
        "reason": ", ".join(reason_parts),
    }
