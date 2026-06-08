"""
Skill: get_missed_calls
Статистика пропущенных звонков и процент перезвонов.
"""
SCHEMA = {
    "name": "get_missed_calls",
    "description": (
        "Статистика пропущенных звонков Utel: сколько пропущено, сколько перезвонили, % потерь. "
        "Используй когда спрашивают о пропущенных звонках, потерянных клиентах, перезвонах."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["today", "yesterday", "this_week", "last_week", "this_month"],
                "description": "Период. По умолчанию — today.",
            }
        },
        "required": [],
    },
}


async def run(params: dict, context: dict) -> dict:
    from app.analytics_utel import get_period_stats
    from app.db import get_session
    from app.periods import PERIOD_FUNCS, PERIOD_LABELS

    key = params.get("period", "today")
    if key not in PERIOD_FUNCS:
        key = "today"

    from_utc, to_utc = PERIOD_FUNCS[key]()

    try:
        async with get_session() as session:
            stats = await get_period_stats(session, from_utc, to_utc)
    except Exception as exc:
        return {"error": str(exc)}

    cb_rate = (
        round(stats.callbacks_done / stats.callbacks_total * 100, 1)
        if stats.callbacks_total > 0 else 0
    )

    # Топ операторов по пропускам
    worst_ops = []
    for ext, op in (stats.operators or {}).items():
        if op.missed > 0:
            worst_ops.append({
                "name": op.name or ext,
                "missed": op.missed,
                "miss_rate": round(op.missed / op.incoming * 100, 1) if op.incoming else 100,
            })
    worst_ops.sort(key=lambda x: x["missed"], reverse=True)

    return {
        "period": PERIOD_LABELS.get(key, key),
        "total_incoming": stats.total_incoming,
        "total_missed": stats.total_missed,
        "miss_rate_pct": stats.miss_rate,
        "callbacks_done": stats.callbacks_done,
        "callbacks_total": stats.callbacks_total,
        "callback_rate_pct": cb_rate,
        "worst_operators": worst_ops[:5],
    }
