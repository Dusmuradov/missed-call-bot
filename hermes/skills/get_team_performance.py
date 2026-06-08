"""
Skill: get_team_performance
Эффективность каждого менеджера: лиды AmoCRM + звонки Utel.
"""
SCHEMA = {
    "name": "get_team_performance",
    "description": (
        "Показатели каждого менеджера/оператора: лиды, обработка, конверсия, звонки. "
        "Используй когда спрашивают кто лучший, у кого низкая конверсия, рейтинг команды."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["today", "yesterday", "this_week", "last_week", "this_month", "last_month", "this_quarter", "last_quarter", "this_year"],
                "description": "Период. По умолчанию — today.",
            }
        },
        "required": [],
    },
}


async def run(params: dict, context: dict) -> dict:
    import asyncio

    from app.amocrm.reports import get_lead_metrics_by_users
    from app.analytics_utel import get_period_stats
    from app.db import get_session
    from app.periods import PERIOD_FUNCS, PERIOD_LABELS

    key = params.get("period", "today")
    if key not in PERIOD_FUNCS:
        key = "today"

    from_utc, to_utc = PERIOD_FUNCS[key]()

    amo_result, utel_stats = await asyncio.gather(
        get_lead_metrics_by_users(from_utc, to_utc),
        _get_utel(from_utc, to_utc),
        return_exceptions=True,
    )

    result = {"period": PERIOD_LABELS.get(key, key), "managers": [], "operators": []}

    if not isinstance(amo_result, Exception) and not amo_result.get("error"):
        users = amo_result.get("users") or {}
        managers = sorted(users.values(), key=lambda x: x["total"], reverse=True)
        result["managers"] = managers
        result["total_leads"] = amo_result.get("total_leads", 0)

    if not isinstance(utel_stats, Exception) and utel_stats:
        ops = []
        for ext, op in (utel_stats.operators or {}).items():
            total_sec = op.total_wait_seconds or 0
        avg_sec = round(op.total_wait_seconds / op.call_count_for_avg) if op.call_count_for_avg else 0
        ops.append({
                "ext": ext,
                "name": op.name or ext,
                "incoming": op.incoming,
                "missed": op.missed,
                "miss_rate": round(op.missed / op.incoming * 100, 1) if op.incoming else 0,
                "callbacks_done": op.callbacks_done,
                "callbacks_total": op.callbacks_total,
                "total_talk_min": round(total_sec / 60, 1),
                "avg_talk_sec": avg_sec,
            })
        result["operators"] = sorted(ops, key=lambda x: x["incoming"], reverse=True)

    return result


async def _get_utel(from_utc, to_utc):
    from app.analytics_utel import get_period_stats
    from app.db import get_session
    async with get_session() as session:
        return await get_period_stats(session, from_utc, to_utc)


