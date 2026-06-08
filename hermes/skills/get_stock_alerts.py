"""
Skill: get_stock_alerts
Критические остатки BILLZ — что нужно заказать срочно.
"""
SCHEMA = {
    "name": "get_stock_alerts",
    "description": (
        "Товары с критическим остатком на складе BILLZ: что заканчивается и требует срочного заказа. "
        "Используй когда спрашивают об остатках, что заканчивается, что надо закупить."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


async def run(params: dict, context: dict) -> dict:
    from datetime import date, timedelta

    from app.billz import aggregator as agg
    from app.billz import reports
    from app.config import settings

    if not settings.billz_secret or not settings.billz_company_id:
        return {"error": "BILLZ не настроен"}

    yesterday = str(date.today() - timedelta(days=1))

    try:
        stock_rows = await reports.get_stock(yesterday)
    except Exception as exc:
        return {"error": str(exc)}

    if not stock_rows:
        return {"date": yesterday, "message": "Данные остатков недоступны"}

    # Velocity из продаж вчера для DoS
    try:
        prod_sales = await reports.get_product_sales(yesterday, yesterday)
        velocity = {
            r.get("product_name", ""): float(r.get("sold_measurement_value") or 0)
            for r in prod_sales
        }
    except Exception:
        velocity = {}

    stock_data = agg.aggregate_stock(stock_rows, velocity)

    return {
        "date": yesterday,
        "stockout_risk": stock_data["stockout_risk"][:10],
        "low_stock": stock_data["low_stock"][:8],
        "overstock_count": len(stock_data["overstock"]),
        "total_retail_value": stock_data["total_retail_value"],
    }
