"""
Skill: get_abc_analysis
ABC-анализ товаров BILLZ по выручке и прибыли.
"""
SCHEMA = {
    "name": "get_abc_analysis",
    "description": (
        "ABC-анализ товаров: группа A (80% выручки), B (15%), C (5% — аутсайдеры). "
        "Используй когда спрашивают какие товары тянут бизнес, что нужно продвигать, что залежалось. "
        "Поддерживает произвольный диапазон: передай start_date и end_date в формате YYYY-MM-DD."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["this_week", "last_week", "this_month", "last_month", "this_quarter", "last_quarter", "this_year"],
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

    start, end, label = resolve_period(params, default="this_month")

    try:
        rows = await reports.get_product_sales(start, end)
    except Exception as exc:
        return {"error": str(exc)}

    if not rows:
        return {"period": label, "message": "Нет данных"}

    # ABC по выручке
    products = []
    for r in rows:
        gross = float(r.get("gross_sales") or 0)
        if gross <= 0:
            continue
        profit = float(r.get("net_profit") or 0)
        qty = float(r.get("sold_measurement_value") or 0)
        margin = float(r.get("average_margin") or 0)
        cats = r.get("product_categories") or []
        categories = ", ".join(str(c) for c in cats) if isinstance(cats, list) and cats else (str(cats) if cats else "—")
        products.append({
            "name": r.get("product_name", "—"),
            "sku": r.get("product_sku") or "",
            "categories": categories,
            "gross_sales": round(gross, 2),
            "net_profit": round(profit, 2),
            "supply_cost": round(gross - profit, 2),
            "margin_pct": round(margin, 1),
            "sold_qty": round(qty, 2),
        })
    products.sort(key=lambda x: x["gross_sales"], reverse=True)

    total_rev = sum(p["gross_sales"] for p in products)
    a, b, c = [], [], []
    cum = 0.0
    for p in products:
        share = cum / total_rev if total_rev else 0
        if share < 0.80:
            a.append(p)
        elif share < 0.95:
            b.append(p)
        else:
            c.append(p)
        cum += p["gross_sales"]

    return {
        "period": label,
        "total_products": len(products),
        "group_A": {"count": len(a), "products": a[:10], "description": "Формируют 80% выручки — приоритет"},
        "group_B": {"count": len(b), "products": b[:8], "description": "Следующие 15% — поддерживать"},
        "group_C": {"count": len(c), "products": c[:10], "description": "Аутсайдеры — акции или вывод"},
        "top3_by_profit": sorted(products, key=lambda x: x["net_profit"], reverse=True)[:3],
    }


