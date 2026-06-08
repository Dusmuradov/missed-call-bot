"""
Обёртки над BILLZ API-эндпоинтами.

Подтверждённые эндпоинты (портированы из billz-sheets-integration/Code.gs):
  GET /v3/order-search  — список заказов (пагинация, limit=50)
  GET /v2/order/{id}    — полные детали заказа

Заглушки (ждут спеки из BILLZ Notion):
  get_stock(), get_imports(), get_suppliers(), get_promos()
"""
from __future__ import annotations

import logging
import re
from typing import AsyncIterator, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_PAGE_LIMIT = 50  # максимум на страницу (как в Code.gs)


# ---------------------------------------------------------------------------
# Заказы (продажи)
# ---------------------------------------------------------------------------

async def iter_orders(start_date: str, end_date: str) -> AsyncIterator[dict]:
    """
    Async-генератор заказов за диапазон дат (формат yyyy-MM-dd).
    Флаттенит orders_sorted_by_date_list[].orders.
    Пропускает удалённые и не-SALE записи.
    Порт логики fetchOrderList_ + syncBillzToSheets из Code.gs.
    """
    from app.billz import client

    if not settings.billz_company_id:
        logger.error("BILLZ_COMPANY_ID не задан — пропускаем iter_orders")
        return

    page = 1
    while True:
        params = {
            "company_id": settings.billz_company_id,
            "start_date": start_date,
            "end_date": end_date,
            "limit": _PAGE_LIMIT,
            "page": page,
        }
        try:
            body = await client.get("/v3/order-search", params=params)
        except Exception as exc:
            logger.error("BILLZ order-search page=%d failed: %s", page, exc)
            break

        groups = body.get("orders_sorted_by_date_list") or []
        orders: list[dict] = []
        for g in groups:
            if isinstance(g.get("orders"), list):
                orders.extend(g["orders"])

        if not orders:
            break

        for order in orders:
            if order.get("deleted"):
                continue
            if order.get("order_type") != "SALE":
                continue
            yield order

        # Последняя страница если пришло меньше лимита
        if len(orders) < _PAGE_LIMIT:
            break
        page += 1


async def get_order_detail(order_id: str) -> Optional[dict]:
    """
    GET /v2/order/{id} — полные детали заказа.
    Порт fetchOrderDetail_() из Code.gs.
    """
    from app.billz import client
    try:
        return await client.get(f"/v2/order/{order_id}")
    except Exception as exc:
        logger.warning("BILLZ order detail %s failed: %s", order_id, exc)
        return None


# ---------------------------------------------------------------------------
# Парсинг данных
# ---------------------------------------------------------------------------

def parse_comment(comment: Optional[str]) -> dict[str, Optional[str]]:
    """
    Извлекает атрибуты канала из комментария заказа.
    Порт parseComment_() из billz-sheets-integration/Code.gs.
    Примеры: "Call center: Азиз | Qayerdan keldi: Instagram"
    """
    if not comment:
        return {"call_center": None, "qayerdan": None}

    cc_match = re.search(r"call[\s_-]*center[:\s]+([^|\n,]+)", comment, re.IGNORECASE)
    q_match = (
        re.search(r"qayerdan[\s_-]*keldi?[:\s]+([^|\n,]+)", comment, re.IGNORECASE)
        or re.search(r"qayerdan[:\s]+([^|\n,]+)", comment, re.IGNORECASE)
    )

    return {
        "call_center": cc_match.group(1).strip() if cc_match else None,
        "qayerdan": q_match.group(1).strip() if q_match else None,
    }


def parse_order_detail(detail: dict) -> dict:
    """
    Нормализует ответ GET /v2/order/{id} в плоский dict.
    Порт buildRows_() из Code.gs — возвращает dict вместо Sheets-строки.
    """
    od = detail.get("order_detail") or {}

    # Итемы заказа
    items: list[dict] = []
    for item in (od.get("order_items") or []):
        product = item.get("product") or {}
        sellers = item.get("sellers") or []
        seller_name: Optional[str] = None
        if sellers:
            seller_name = ((sellers[0].get("seller") or {}).get("name"))
        items.append({
            "category": product.get("category_name"),
            "name": product.get("name") or product.get("base_name"),
            "seller": seller_name,
            "price": float(item.get("price") or item.get("sale_price") or 0),
            "qty": float(item.get("measurement_value") or 0),
        })

    # Оплата: split на наличные/карта (порт логики из Code.gs)
    cash = 0.0
    card = 0.0
    for pmt in (od.get("order_payments") or []):
        ptype = ((pmt.get("company_payment_type") or {}).get("name") or "")
        amount = float(pmt.get("paid_amount") or 0)
        if re.search(r"naqt|наличн", ptype, re.IGNORECASE):
            cash += amount
        else:
            card += amount

    # Канальная атрибуция из комментария
    channel = parse_comment(od.get("comment"))

    return {
        "order_number": detail.get("order_number"),
        "sold_at": (
            od.get("display_sold_at")
            or od.get("created_at")
            or detail.get("display_sold_at")
        ),
        "total": float(od.get("total_price") or 0),
        "total_qty": float(
            od.get("total_products_measurement_value")
            or sum(i["qty"] for i in items)
        ),
        "shop": (od.get("shop") or {}).get("name"),
        "customer_phone": (
            (od.get("customer") or {}).get("phone")
            or (od.get("customer") or {}).get("name")
        ),
        "cash": cash,
        "card": card,
        "call_center": channel["call_center"],
        "qayerdan": channel["qayerdan"],
        "items": items,
    }


# ---------------------------------------------------------------------------
# Вспомогательная функция: базовые параметры отчётов
# ---------------------------------------------------------------------------

def _report_params(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    report_date: Optional[str] = None,
    page: int = 1,
    limit: int = 1000,
    extra: Optional[dict] = None,
) -> dict:
    """Собирает стандартный dict параметров для report-эндпоинтов."""
    p: dict = {"page": page, "limit": limit, "currency": settings.billz_currency}
    if start_date:
        p["start_date"] = start_date
    if end_date:
        p["end_date"] = end_date
    if report_date:
        p["report_date"] = report_date
    if settings.billz_shop_ids:
        p["shop_ids"] = settings.billz_shop_ids
    if extra:
        p.update(extra)
    return p


# ---------------------------------------------------------------------------
# Остатки по товарам — GET /v1/stock-report-table
# ---------------------------------------------------------------------------

async def get_stock(report_date: str) -> list[dict]:
    """
    Остатки на конец дня (report_date = YYYY-MM-DD).
    Возвращает: [{product_name, product_sku, supplier_name, measurement_value,
                  retail_price, supply_price, estimated_income, estimated_margin, ...}]
    """
    from app.billz import client
    rows: list[dict] = []
    page = 1
    while True:
        params = _report_params(report_date=report_date, page=page, limit=1000)
        try:
            body = await client.get("/v1/stock-report-table", params=params)
        except Exception as exc:
            logger.error("BILLZ stock-report-table page=%d failed: %s", page, exc)
            break
        page_rows = body.get("rows") or []
        rows.extend(page_rows)
        total = body.get("count") or 0
        if len(rows) >= total or len(page_rows) == 0:
            break
        page += 1
    logger.info("BILLZ: got %d stock rows for %s", len(rows), report_date)
    return rows


# ---------------------------------------------------------------------------
# Продажи по товарам — GET /v1/product-general-table
# ---------------------------------------------------------------------------

async def get_product_sales(start_date: str, end_date: str) -> list[dict]:
    """
    Продажи по товарам за диапазон дат.
    Возвращает: [{product_name, product_sku, product_categories,
                  sold_measurement_value, gross_sales, net_profit,
                  average_margin, returned_measurement_value, ...}]
    """
    from app.billz import client
    rows: list[dict] = []
    page = 1
    while True:
        params = _report_params(start_date=start_date, end_date=end_date, page=page, limit=1000)
        try:
            body = await client.get("/v1/product-general-table", params=params)
        except Exception as exc:
            logger.error("BILLZ product-general-table page=%d failed: %s", page, exc)
            break
        page_rows = body.get("rows") or body.get("data") or []
        if isinstance(page_rows, list):
            rows.extend(page_rows)
        else:
            break
        # Завершаем если данных меньше лимита
        if len(page_rows) < 1000:
            break
        page += 1
    logger.info("BILLZ: got %d product-sales rows for %s–%s", len(rows), start_date, end_date)
    return rows


# ---------------------------------------------------------------------------
# Сводный отчёт — GET /v1/general-report (итоги)
# ---------------------------------------------------------------------------

async def get_summary(start_date: str, end_date: str) -> Optional[dict]:
    """
    Итоговые показатели за период (одна строка, не пагинированная).
    Возвращает: {gross_sales, net_gross_sales, gross_profit, average_cheque,
                 products_sold, transactions_count, average_extra_charge, ...}
    """
    from app.billz import client
    params = _report_params(start_date=start_date, end_date=end_date, limit=1)
    try:
        body = await client.get("/v1/general-report", params=params)
        return body
    except Exception as exc:
        logger.error("BILLZ general-report failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Импорты — GET /v1/import-report-table
# ---------------------------------------------------------------------------

async def get_imports(start_date: str, end_date: str) -> list[dict]:
    """
    Импорты (поступления товаров) за период.
    import_type=import — только импорты (не заказы поставщикам).
    """
    from app.billz import client
    params = _report_params(
        start_date=start_date, end_date=end_date, limit=500,
        extra={"import_type": "import"},
    )
    try:
        body = await client.get("/v1/import-report-table", params=params)
        return body.get("rows") or body.get("data") or []
    except Exception as exc:
        logger.error("BILLZ import-report-table failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Продажи по поставщикам — GET /v1/product-sells-by-suppliers-table
# ---------------------------------------------------------------------------

async def get_supplier_sales(start_date: str, end_date: str) -> list[dict]:
    """
    Продажи с разбивкой по поставщикам за период.
    Поля: supplier_name, product_name, sold_measurement_value, gross_sales, net_profit.
    """
    from app.billz import client
    rows: list[dict] = []
    page = 1
    while True:
        params = _report_params(start_date=start_date, end_date=end_date, page=page, limit=1000)
        try:
            body = await client.get("/v1/product-sells-by-suppliers-table", params=params)
        except Exception as exc:
            logger.error("BILLZ product-sells-by-suppliers-table page=%d failed: %s", page, exc)
            break
        page_rows = body.get("rows") or body.get("data") or []
        if not isinstance(page_rows, list):
            break
        rows.extend(page_rows)
        if len(page_rows) < 1000:
            break
        page += 1
    logger.info("BILLZ: got %d supplier-sales rows for %s–%s", len(rows), start_date, end_date)
    return rows


# ---------------------------------------------------------------------------
# Акции — TODO (endpoint не найден в документации)
# ---------------------------------------------------------------------------

async def get_promos() -> Optional[list[dict]]:
    """
    TODO: список активных акций.
    Endpoint не найден в BILLZ Notion API docs (Отчеты.pdf).
    Заглушка — блок Акции в дайджесте работает на основе AI-анализа продаж.
    """
    return None
