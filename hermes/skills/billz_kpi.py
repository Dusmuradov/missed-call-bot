"""
Skill: get_billz_kpi
DeepSeek вызывает этот инструмент когда нужны данные продаж из BILLZ за конкретную дату.
"""
from __future__ import annotations

SCHEMA = {
    "name": "get_billz_kpi",
    "description": (
        "Получает KPI продаж из BILLZ POS (выручка, прибыль, кол-во заказов, средний чек). "
        "Используй когда пользователь спрашивает о продажах, выручке или показателях магазина."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "Дата в формате YYYY-MM-DD. Если не указана — берётся вчера.",
            },
        },
        "required": [],
    },
}


async def run(params: dict, context: dict) -> dict:
    from datetime import date, timedelta

    from app.billz import reports
    from app.config import settings

    if not settings.billz_secret or not settings.billz_company_id:
        return {"error": "BILLZ не настроен (нет BILLZ_SECRET или BILLZ_COMPANY_ID)"}

    date_str = params.get("date") or str(date.today() - timedelta(days=1))

    try:
        summary = await reports.get_summary(date_str, date_str)
        if not summary:
            return {"date": date_str, "error": "Нет данных за эту дату"}

        return {
            "date": date_str,
            "gross_sales": summary.get("gross_sales"),
            "net_profit": summary.get("gross_profit"),
            "transactions": summary.get("transactions_count"),
            "average_cheque": summary.get("average_cheque"),
            "products_sold": summary.get("products_sold"),
        }
    except Exception as exc:
        return {"error": str(exc)}
