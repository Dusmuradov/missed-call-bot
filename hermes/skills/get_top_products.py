"""
Skill: get_top_products
Топ и аутсайдеры по продажам BILLZ с маржинальностью.
"""
SCHEMA = {
    "name": "get_top_products",
    "description": (
        "Топ товаров по выручке и прибыли из BILLZ, аутсайдеры, товары с низкой маржой. "
        "Используй когда спрашивают что продаётся лучше всего, какие товары прибыльные, аутсайдеры. "
        "Поддерживает произвольный диапазон: передай start_date и end_date в формате YYYY-MM-DD."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["today", "yesterday", "this_week", "last_week", "this_month", "last_month", "this_quarter", "last_quarter", "this_year"],
                "description": "Именованный период. Игнорируется если заданы start_date и end_date.",
            },
            "start_date": {
                "type": "string",
                "description": "Начало диапазона YYYY-MM-DD.",
            },
            "end_date": {
                "type": "string",
                "description": "Конец диапазона YYYY-MM-DD включительно.",
            },
            "limit": {
                "type": "integer",
                "description": "Количество позиций в топе. По умолчанию 10.",
            },
        },
        "required": [],
    },
}


async def run(params: dict, context: dict) -> dict:
    from app.billz import aggregator as agg
    from app.billz import reports
    from app.config import settings
    from app.periods import resolve_period

    if not settings.billz_secret or not settings.billz_company_id:
        return {"error": "BILLZ не настроен"}

    start, end, label = resolve_period(params, default="yesterday")
    limit = min(int(params.get("limit") or 10), 20)

    try:
        rows = await reports.get_product_sales(start, end)
        if not rows:
            rows = await reports.get_customer_purchases(start, end, with_customers=False)
    except Exception as exc:
        return {"error": str(exc)}

    if not rows:
        return {"period": label, "message": "Нет данных продаж в BILLZ за этот период"}

    data = agg.aggregate_product_sales(rows)

    return {
        "period": label,
        "top_by_profit": data["top_by_profit"][:limit],
        "low_margin_products": data["low_margin"][:5],
        "high_return_rate": data["high_return_rate"][:3],
        "total_net_profit": data["total_net_profit"],
        "total_gross_sales": data["total_gross_sales"],
    }


