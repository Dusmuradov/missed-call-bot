"""
Skill: debug_billz_fields
Диагностика: показывает все поля из сырых ответов BILLZ API.
Используется чтобы понять какие данные реально возвращает API.
"""
SCHEMA = {
    "name": "debug_billz_fields",
    "description": (
        "Показывает все поля (ключи и примеры значений) из сырых ответов BILLZ API: "
        "остатки (stock-report-table) и продажи по товарам (product-general-table). "
        "Используй только если нужно проверить какие данные доступны из BILLZ."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


async def run(params: dict, context: dict) -> dict:
    from datetime import date, timedelta

    from app.billz import client
    from app.billz.reports import _report_params
    from app.config import settings

    if not settings.billz_secret or not settings.billz_company_id:
        return {"error": "BILLZ не настроен"}

    yesterday = str(date.today() - timedelta(days=1))
    result: dict = {}

    # Один ряд из stock-report-table
    try:
        params_stock = _report_params(report_date=yesterday, page=1, limit=1)
        body = await client.get("/v1/stock-report-table", params=params_stock)
        rows = body.get("rows") or []
        if rows:
            row = rows[0]
            result["stock_fields"] = {
                k: (str(v)[:80] if v is not None else None)
                for k, v in row.items()
            }
        else:
            result["stock_fields"] = "нет строк"
    except Exception as exc:
        result["stock_fields"] = f"ошибка: {exc}"

    # Один ряд из product-general-table
    week_ago = str(date.today() - timedelta(days=7))
    try:
        params_sales = _report_params(start_date=week_ago, end_date=yesterday, page=1, limit=1)
        body = await client.get("/v1/product-general-table", params=params_sales)
        rows = body.get("rows") or body.get("data") or []
        if rows:
            row = rows[0]
            result["product_sales_fields"] = {
                k: (str(v)[:80] if v is not None else None)
                for k, v in row.items()
            }
        else:
            result["product_sales_fields"] = "нет строк"
    except Exception as exc:
        result["product_sales_fields"] = f"ошибка: {exc}"

    return result
