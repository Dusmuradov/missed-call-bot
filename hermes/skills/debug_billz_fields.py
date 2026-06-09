"""
Skill: debug_billz_fields
Diagnostics — shows raw fields and sample values from multiple BILLZ endpoints.
Used to find which endpoints return data and diagnose empty results.
"""
SCHEMA = {
    "name": "debug_billz_fields",
    "description": (
        "Диагностика BILLZ: проверяет какие эндпоинты возвращают данные и какие поля доступны. "
        "Тестирует: заказы (order-search), остатки, продажи, покупки клиентов, продавцы, сводный отчёт. "
        "Используй только для отладки и диагностики API."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


async def run(params: dict, context: dict) -> dict:
    from datetime import date, timedelta

    from app.billz import client
    from app.billz.reports import _report_params
    from app.config import settings

    if not settings.billz_secret or not settings.billz_company_id:
        return {"error": "BILLZ не настроен (нет BILLZ_SECRET или BILLZ_COMPANY_ID)"}

    yesterday = str(date.today() - timedelta(days=1))
    month_ago = str(date.today() - timedelta(days=30))
    result: dict = {}

    # 0. order-search (legacy orders API) — check if any orders exist
    try:
        body = await client.get("/v3/order-search", params={
            "company_id": settings.billz_company_id,
            "start_date": month_ago,
            "end_date": yesterday,
            "limit": 3,
            "page": 1,
        })
        groups = body.get("orders_sorted_by_date_list") or []
        orders = []
        for g in groups:
            orders.extend(g.get("orders") or [])
        result["0_order_search"] = {
            "status": f"OK — нашлось {len(orders)} заказов за 30 дней",
            "response_keys": list(body.keys()),
            "sample_order_fields": list(orders[0].keys()) if orders else "нет заказов",
        }
    except Exception as exc:
        result["0_order_search"] = {"status": f"ошибка: {exc}"}

    # 1. stock-report-table
    try:
        body = await client.get("/v1/stock-report-table",
                                params=_report_params(report_date=yesterday, page=1, limit=3))
        rows = body.get("rows") or []
        result["1_stock"] = {
            "status": f"OK — {len(rows)} строк",
            "fields": list(rows[0].keys()) if rows else "нет строк",
        }
    except Exception as exc:
        result["1_stock"] = {"status": f"ошибка: {exc}"}

    # 2. general-report (summary)
    try:
        body = await client.get("/v1/general-report",
                                params=_report_params(start_date=month_ago, end_date=yesterday, limit=1))
        result["2_general_report"] = {
            "status": "OK",
            "response_keys": list(body.keys()),
            "raw": {k: str(v)[:80] for k, v in body.items()},
        }
    except Exception as exc:
        result["2_general_report"] = {"status": f"ошибка: {exc}"}

    async def probe(label: str, endpoint: str, params_fn, row_key: str = "rows"):
        try:
            body = await client.get(endpoint, params=params_fn())
            # Try direct key, then wrapped
            rows = body.get(row_key)
            if not rows and isinstance(body.get("data"), dict):
                rows = body["data"].get(row_key)
            if not rows and isinstance(body.get("data"), list):
                rows = body["data"]
            rows = rows or []
            if rows:
                result[label] = {
                    "status": f"OK — {len(rows)} строк",
                    "fields": list(rows[0].keys()),
                }
            else:
                result[label] = {
                    "status": "пусто (0 строк)",
                    "response_keys": list(body.keys()),
                    "raw_sample": {k: str(v)[:60] for k, v in list(body.items())[:6]},
                }
        except Exception as exc:
            result[label] = {"status": f"ошибка: {exc}"}

    # 3. product-general-table
    await probe("3_product_sales", "/v1/product-general-table",
                lambda: _report_params(start_date=month_ago, end_date=yesterday, page=1, limit=3))

    # 4. customer-purchases-table (key="puchases" — API typo)
    await probe("4_customer_purchases", "/v1/customer-purchases-table",
                lambda: {
                    **_report_params(start_date=month_ago, end_date=yesterday, page=1, limit=3),
                    "with_customers": "false",
                },
                row_key="puchases")

    # 5. seller-general-table
    await probe("5_sellers", "/v1/seller-general-table",
                lambda: _report_params(start_date=month_ago, end_date=yesterday, page=1, limit=3))

    # 6. product-general-report (summary, not paginated)
    try:
        body = await client.get("/v1/product-general-report",
                                params=_report_params(start_date=month_ago, end_date=yesterday, limit=1))
        result["6_product_summary"] = {
            "status": "OK",
            "response_keys": list(body.keys()),
            "raw": {k: str(v)[:80] for k, v in body.items()},
        }
    except Exception as exc:
        result["6_product_summary"] = {"status": f"ошибка: {exc}"}

    return result
