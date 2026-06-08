"""
Skill: get_sales_period
Сводные продажи BILLZ за любой период.
"""
SCHEMA = {
    "name": "get_sales_period",
    "description": (
        "Выручка, прибыль, кол-во транзакций из BILLZ за выбранный период. "
        "Используй когда спрашивают сколько продали за неделю/месяц, какая выручка, сколько заработали."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["today", "yesterday", "this_week", "last_week", "this_month", "last_month", "this_quarter", "last_quarter", "this_year", "last_year"],
                "description": "Период. По умолчанию — yesterday.",
            }
        },
        "required": [],
    },
}


async def run(params: dict, context: dict) -> dict:
    from datetime import timezone
    from zoneinfo import ZoneInfo

    from app.billz import reports
    from app.config import settings
    from app.periods import PERIOD_FUNCS, PERIOD_LABELS

    if not settings.billz_secret or not settings.billz_company_id:
        return {"error": "BILLZ не настроен"}

    key = params.get("period", "yesterday")
    if key not in PERIOD_FUNCS:
        key = "yesterday"

    _TZ = ZoneInfo("Asia/Tashkent")
    _UTC = timezone.utc
    from_utc, to_utc = PERIOD_FUNCS[key]()
    start = from_utc.replace(tzinfo=_UTC).astimezone(_TZ).strftime("%Y-%m-%d")
    end = to_utc.replace(tzinfo=_UTC).astimezone(_TZ).strftime("%Y-%m-%d")

    try:
        summary = await reports.get_summary(start, end)
    except Exception as exc:
        return {"error": str(exc)}

    if not summary:
        return {"period": PERIOD_LABELS.get(key, key), "message": "Нет данных BILLZ"}

    return {
        "period": PERIOD_LABELS.get(key, key),
        "gross_sales": summary.get("gross_sales"),
        "net_profit": summary.get("gross_profit"),
        "transactions": summary.get("transactions_count"),
        "average_cheque": summary.get("average_cheque"),
        "products_sold": summary.get("products_sold"),
        "net_gross_sales": summary.get("net_gross_sales"),
    }
