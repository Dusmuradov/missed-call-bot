"""
Skill: get_sales_period
Сводные продажи BILLZ за любой период.
"""
SCHEMA = {
    "name": "get_sales_period",
    "description": (
        "Выручка, прибыль, кол-во транзакций из BILLZ за выбранный период. "
        "Используй когда спрашивают сколько продали за неделю/месяц, какая выручка, сколько заработали. "
        "Поддерживает произвольный диапазон: передай start_date и end_date в формате YYYY-MM-DD."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["today", "yesterday", "this_week", "last_week", "this_month", "last_month", "this_quarter", "last_quarter", "this_year", "last_year"],
                "description": "Именованный период. Игнорируется если заданы start_date и end_date.",
            },
            "start_date": {
                "type": "string",
                "description": "Начало диапазона YYYY-MM-DD. Вычисли из запроса пользователя (например 'последние 3.5 месяца' → сегодня минус ~105 дней).",
            },
            "end_date": {
                "type": "string",
                "description": "Конец диапазона YYYY-MM-DD включительно.",
            },
        },
        "required": [],
    },
}


async def run(params: dict, context: dict) -> dict:
    from app.billz import reports
    from app.config import settings
    from app.periods import resolve_period

    if not settings.billz_secret or not settings.billz_company_id:
        return {"error": "BILLZ не настроен"}

    start, end, label = resolve_period(params, default="yesterday")

    try:
        summary = await reports.get_summary(start, end)
    except Exception as exc:
        return {"error": str(exc)}

    if not summary:
        return {"period": label, "message": "Нет данных BILLZ"}

    return {
        "period": label,
        "gross_sales": summary.get("gross_sales"),
        "net_gross_sales": summary.get("net_gross_sales"),
        "net_profit": summary.get("gross_profit"),
        "transactions": summary.get("transactions_count"),
        "average_cheque": summary.get("average_cheque"),
        "average_extra_charge": summary.get("average_extra_charge"),
        "products_sold": summary.get("products_sold"),
    }
