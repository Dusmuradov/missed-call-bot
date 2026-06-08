"""
Skill: get_top_products
Топ и аутсайдеры по продажам BILLZ с маржинальностью.
"""
SCHEMA = {
    "name": "get_top_products",
    "description": (
        "Топ товаров по выручке и прибыли из BILLZ, аутсайдеры, товары с низкой маржой. "
        "Используй когда спрашивают что продаётся лучше всего, какие товары прибыльные, аутсайдеры."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["today", "yesterday", "this_week", "last_week", "this_month", "last_month", "this_quarter", "last_quarter", "this_year"],
                "description": "Период. По умолчанию — yesterday.",
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
    from app.periods import PERIOD_FUNCS, PERIOD_LABELS

    if not settings.billz_secret or not settings.billz_company_id:
        return {"error": "BILLZ не настроен"}

    key = params.get("period", "yesterday")
    if key not in PERIOD_FUNCS:
        key = "yesterday"
    limit = min(int(params.get("limit") or 10), 20)

    from_utc, to_utc = PERIOD_FUNCS[key]()
    start = from_utc.strftime("%Y-%m-%d")
    end = to_utc.strftime("%Y-%m-%d")

    try:
        rows = await reports.get_product_sales(start, end)
    except Exception as exc:
        return {"error": str(exc)}

    if not rows:
        return {"period": PERIOD_LABELS.get(key, key), "message": "Нет данных продаж"}

    data = agg.aggregate_product_sales(rows)

    return {
        "period": PERIOD_LABELS.get(key, key),
        "top_by_profit": data["top_by_profit"][:limit],
        "low_margin_products": data["low_margin"][:5],
        "high_return_rate": data["high_return_rate"][:3],
        "total_net_profit": data["total_net_profit"],
        "total_gross_sales": data["total_gross_sales"],
    }


