"""
Skill: debug_billz_fields
Diagnostics — shows raw fields and sample values from multiple BILLZ endpoints.
Used to verify which endpoints return data for this company.
"""
SCHEMA = {
    "name": "debug_billz_fields",
    "description": (
        "Диагностика BILLZ: проверяет какие эндпоинты возвращают данные и какие поля доступны. "
        "Тестирует: остатки, продажи по товарам, покупки клиентов, продавцы, сводный отчёт. "
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

    async def probe(label: str, endpoint: str, params_fn, row_key: str = "rows"):
        try:
            body = await client.get(endpoint, params=params_fn())
            rows = body.get(row_key) or []
            if rows:
                row = rows[0]
                result[label] = {
                    "status": f"OK — {len(rows)} строк",
                    "fields": {k: (str(v)[:60] if v is not None else None) for k, v in row.items()},
                }
            else:
                result[label] = {
                    "status": "пусто (0 строк)",
                    "response_keys": list(body.keys()),
                    "raw_sample": {k: str(v)[:80] for k, v in list(body.items())[:5]},
                }
        except Exception as exc:
            result[label] = {"status": f"ошибка: {exc}"}

    # stock-report-table
    await probe(
        "1_stock",
        "/v1/stock-report-table",
        lambda: _report_params(report_date=yesterday, page=1, limit=3),
    )

    # product-general-table (product sales)
    await probe(
        "2_product_sales",
        "/v1/product-general-table",
        lambda: _report_params(start_date=month_ago, end_date=yesterday, page=1, limit=3),
    )

    # customer-purchases-table (alternative product sales source, key="puchases")
    await probe(
        "3_customer_purchases",
        "/v1/customer-purchases-table",
        lambda: {
            **_report_params(start_date=month_ago, end_date=yesterday, page=1, limit=3),
            "with_customers": "false",
        },
        row_key="puchases",
    )

    # seller-general-table
    await probe(
        "4_sellers",
        "/v1/seller-general-table",
        lambda: _report_params(start_date=month_ago, end_date=yesterday, page=1, limit=3),
    )

    # general-report (summary totals)
    try:
        body = await client.get(
            "/v1/general-report",
            params=_report_params(start_date=month_ago, end_date=yesterday, limit=1),
        )
        result["5_general_summary"] = {
            "status": "OK",
            "data": {k: str(v)[:80] for k, v in body.items()},
        }
    except Exception as exc:
        result["5_general_summary"] = {"status": f"ошибка: {exc}"}

    return result
