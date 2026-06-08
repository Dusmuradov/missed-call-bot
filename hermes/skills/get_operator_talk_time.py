"""
Skill: get_operator_talk_time
Время разговора по каждому оператору за период.
"""
SCHEMA = {
    "name": "get_operator_talk_time",
    "description": (
        "Время разговора (talk time) по каждому оператору/менеджеру за период: "
        "суммарное и среднее. "
        "Используй когда спрашивают время разговора, сколько времени говорил оператор, "
        "средняя длительность звонка, кто больше всего разговаривал."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["today", "yesterday", "this_week", "last_week", "this_month"],
                "description": "Период. По умолчанию — this_month.",
            }
        },
        "required": [],
    },
}


async def run(params: dict, context: dict) -> dict:
    from app.analytics_utel import get_period_stats
    from app.db import get_session
    from app.periods import PERIOD_FUNCS, PERIOD_LABELS

    key = params.get("period", "this_month")
    if key not in PERIOD_FUNCS:
        key = "this_month"

    from_utc, to_utc = PERIOD_FUNCS[key]()

    try:
        async with get_session() as session:
            stats = await get_period_stats(session, from_utc, to_utc)
    except Exception as exc:
        return {"error": str(exc)}

    operators = []
    for op in (stats.operators or {}).values():
        total_sec = op.total_wait_seconds or 0
        total_calls = op.incoming + op.outgoing
        answered = op.answered
        avg_sec = round(op.total_wait_seconds / op.call_count_for_avg) if op.call_count_for_avg else 0

        operators.append({
            "name": op.name,
            "total_talk_min": round(total_sec / 60, 1),
            "total_talk_sec": total_sec,
            "avg_talk_sec": avg_sec,
            "avg_talk_min": round(avg_sec / 60, 1),
            "answered_calls": answered,
            "total_calls": total_calls,
            "missed": op.missed,
        })

    operators.sort(key=lambda x: x["total_talk_sec"], reverse=True)

    total_sec_all = sum(o["total_talk_sec"] for o in operators)

    return {
        "period": PERIOD_LABELS.get(key, key),
        "operators": operators,
        "total_talk_min_all": round(total_sec_all / 60, 1),
    }
