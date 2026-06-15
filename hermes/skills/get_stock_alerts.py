"""
Skill: get_stock_alerts
Критические остатки BILLZ — что нужно заказать срочно.
"""
SCHEMA = {
    "name": "get_stock_alerts",
    "description": (
        "Товары с критическим остатком на складе BILLZ: что заканчивается и требует срочного заказа. "
        "Используй когда спрашивают об остатках, что заканчивается, что надо закупить, какие товары закончатся. "
        "Параметр velocity_days задаёт за сколько дней считать темп продаж (по умолчанию 30). "
        "Для вопроса 'за последние 3 месяца' передай velocity_days=90."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "velocity_days": {
                "type": "integer",
                "description": (
                    "За сколько последних дней считать среднедневной темп продаж для расчёта Days of Supply. "
                    "По умолчанию 30. Для '3 месяца' → 90, для '3.5 месяца' → 105."
                ),
            },
            "stock_date": {
                "type": "string",
                "description": "Дата остатков YYYY-MM-DD по Ташкенту (UTC+5). По умолчанию — вчера по Ташкенту.",
            },
        },
        "required": [],
    },
}


async def run(params: dict, context: dict) -> dict:
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from datetime import datetime

    from app.billz import aggregator as agg
    from app.billz import reports
    from app.config import settings

    if not settings.billz_secret or not settings.billz_company_id:
        return {"error": "BILLZ не настроен"}

    _today_tashkent = datetime.now(ZoneInfo("Asia/Tashkent")).date()
    stock_date = params.get("stock_date", "").strip() or str(_today_tashkent - timedelta(days=1))
    velocity_days = max(1, min(int(params.get("velocity_days") or 30), 365))

    try:
        stock_rows = await reports.get_stock(stock_date)
    except Exception as exc:
        return {"error": str(exc)}

    if not stock_rows:
        return {"date": stock_date, "message": "Данные остатков недоступны"}

    # Velocity: среднедневной темп продаж за velocity_days
    vel_end = stock_date
    vel_start = str(date.fromisoformat(stock_date) - timedelta(days=velocity_days - 1))
    velocity: dict[str, float] = {}
    try:
        prod_sales = await reports.get_product_sales(vel_start, vel_end)
        if not prod_sales:
            # Fallback: customer-purchases-table использует другую выборку и может вернуть данные
            prod_sales = await reports.get_customer_purchases(vel_start, vel_end, with_customers=False)
        velocity = {
            r.get("product_name", ""): float(r.get("sold_measurement_value") or 0) / velocity_days
            for r in prod_sales
            if r.get("product_name")
        }
    except Exception:
        velocity = {}

    stock_data = agg.aggregate_stock(stock_rows, velocity)

    return {
        "date": stock_date,
        "velocity_period_days": velocity_days,
        "stockout_risk": stock_data["stockout_risk"][:10],
        "low_stock": stock_data["low_stock"][:8],
        "overstock": stock_data["overstock"][:15],
        "total_retail_value": stock_data["total_retail_value"],
    }
