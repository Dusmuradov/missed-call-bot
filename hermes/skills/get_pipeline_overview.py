"""
Skill: get_pipeline_overview
Обзор воронки AmoCRM: лиды, конверсия, необработанные.
"""
SCHEMA = {
    "name": "get_pipeline_overview",
    "description": (
        "Обзор воронки продаж AmoCRM: сколько лидов, сколько необработанных, конверсия. "
        "Используй когда спрашивают о состоянии воронки, новых заявках, необработанных лидах."
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
    from app.amocrm.reports import get_lead_metrics
    from app.periods import PERIOD_FUNCS, PERIOD_LABELS

    key = params.get("period", "today")
    if key not in PERIOD_FUNCS:
        key = "today"

    from_utc, to_utc = PERIOD_FUNCS[key]()
    metrics = await get_lead_metrics(from_utc, to_utc)

    if metrics.get("error"):
        return {"error": metrics["error"]}

    total = metrics.get("total_leads", 0)
    unprocessed = metrics.get("unprocessed_leads", 0)
    processed = metrics.get("processed_leads", 0)
    conv = metrics.get("conversion_rate", 0)

    return {
        "period": PERIOD_LABELS.get(key, key),
        "total_leads": total,
        "unprocessed": unprocessed,
        "processed": processed,
        "conversion_rate": conv,
        "work_hours_leads": metrics.get("work_hours", 0),
        "non_work_hours_leads": metrics.get("non_work_hours", 0),
        "by_pipeline": metrics.get("by_pipeline", {}),
    }
