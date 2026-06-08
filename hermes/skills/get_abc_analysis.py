"""
Skill: get_abc_analysis
ABC-анализ товаров BILLZ по выручке и прибыли.
"""
SCHEMA = {
    "name": "get_abc_analysis",
    "description": (
        "ABC-анализ товаров: группа A (80% выручки), B (15%), C (5% — аутсайдеры). "
        "Используй когда спрашивают какие товары тянут бизнес, что нужно продвигать, что залежалось."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["this_week", "last_week", "this_month", "last_month", "this_quarter", "last_quarter", "this_year"],
                "description": "Период для анализа. По умолчанию — this_month.",
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

    key = params.get("period", "this_month")
    if key not in PERIOD_FUNCS:
        key = "this_month"

    _TZ = ZoneInfo("Asia/Tashkent")
    _UTC = timezone.utc
    from_utc, to_utc = PERIOD_FUNCS[key]()
    start = from_utc.replace(tzinfo=_UTC).astimezone(_TZ).strftime("%Y-%m-%d")
    end = to_utc.replace(tzinfo=_UTC).astimezone(_TZ).strftime("%Y-%m-%d")

    try:
        rows = await reports.get_product_sales(start, end)
    except Exception as exc:
        return {"error": str(exc)}

    if not rows:
        return {"period": PERIOD_LABELS.get(key, key), "message": "Нет данных"}

    # ABC по выручке
    products = [
        {
            "name": r.get("product_name", "—"),
            "gross_sales": float(r.get("gross_sales") or 0),
            "net_profit": float(r.get("net_profit") or 0),
            "margin_pct": float(r.get("average_margin") or 0),
            "qty": float(r.get("sold_measurement_value") or 0),
        }
        for r in rows if float(r.get("gross_sales") or 0) > 0
    ]
    products.sort(key=lambda x: x["gross_sales"], reverse=True)

    total_rev = sum(p["gross_sales"] for p in products)
    a, b, c = [], [], []
    cum = 0.0
    for p in products:
        share = cum / total_rev if total_rev else 0
        if share < 0.80:
            a.append(p["name"])
        elif share < 0.95:
            b.append(p["name"])
        else:
            c.append(p["name"])
        cum += p["gross_sales"]

    return {
        "period": PERIOD_LABELS.get(key, key),
        "total_products": len(products),
        "group_A": {"count": len(a), "products": a[:10], "description": "Формируют 80% выручки — приоритет"},
        "group_B": {"count": len(b), "products": b[:8], "description": "Следующие 15% — поддерживать"},
        "group_C": {"count": len(c), "products": c[:10], "description": "Аутсайдеры — акции или вывод"},
        "top3_by_profit": sorted(products, key=lambda x: x["net_profit"], reverse=True)[:3],
    }


