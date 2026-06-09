"""
BILLZ API wrappers — orders + all report endpoints.

Order endpoints:
  GET /v3/order-search                       — order list (paginated)
  GET /v2/order/{id}                         — full order detail

Report endpoints (source: Отчеты.pdf):
  GET /v1/stock-report-table                 — current stock levels         → rows["rows"]
  GET /v1/product-general-table              — product sales                → rows["rows"]
  GET /v1/product-general-report             — product sales totals         → object
  GET /v1/general-report                     — period summary totals        → object
  GET /v1/general-report-table               — period table by day/week/month → rows["rows"]
  GET /v1/transaction-report-table           — individual transactions      → rows["rows"]
  GET /v1/report-product-performance-table   — product effectiveness        → rows["rows"]
  GET /v1/import-report-table                — imports / purchases          → rows["rows"]
  GET /v1/product-sells-by-suppliers-table   — sales by supplier            → rows["rows"]
  GET /v1/supplier-order-return-report-table — order returns                → rows["rows"]
  GET /v1/stocktaking-summary-table          — stocktaking results          → rows["rows"]
  GET /v1/write-off-report-table             — write-offs                   → rows["Items"] (capital I)
  GET /v1/seller-general-table               — seller performance           → rows["rows"]
  GET /v1/customer-general-table             — customer analytics           → rows["rows"]
  GET /v1/customer-purchases-table           — customer purchases per item  → rows["puchases"] (API typo)
"""
from __future__ import annotations

import logging
import re
from typing import AsyncIterator, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_PAGE_LIMIT = 50


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

async def iter_orders(start_date: str, end_date: str) -> AsyncIterator[dict]:
    """Async generator of SALE orders for a date range (yyyy-MM-dd)."""
    from app.billz import client

    if not settings.billz_company_id:
        logger.error("BILLZ_COMPANY_ID not set — skipping iter_orders")
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

        if len(orders) < _PAGE_LIMIT:
            break
        page += 1


async def get_order_detail(order_id: str) -> Optional[dict]:
    """GET /v2/order/{id} — full order detail."""
    from app.billz import client
    try:
        return await client.get(f"/v2/order/{order_id}")
    except Exception as exc:
        logger.warning("BILLZ order detail %s failed: %s", order_id, exc)
        return None


# ---------------------------------------------------------------------------
# Order parsing helpers
# ---------------------------------------------------------------------------

def parse_comment(comment: Optional[str]) -> dict[str, Optional[str]]:
    """Extract channel attributes from order comment.
    Example: "Call center: Азиз | Qayerdan keldi: Instagram"
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
    """Normalize GET /v2/order/{id} response into a flat dict."""
    od = detail.get("order_detail") or {}

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

    cash = 0.0
    card = 0.0
    for pmt in (od.get("order_payments") or []):
        ptype = ((pmt.get("company_payment_type") or {}).get("name") or "")
        amount = float(pmt.get("paid_amount") or 0)
        if re.search(r"naqt|наличн", ptype, re.IGNORECASE):
            cash += amount
        else:
            card += amount

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
# Report helpers
# ---------------------------------------------------------------------------

def _report_params(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    report_date: Optional[str] = None,
    page: int = 1,
    limit: int = 1000,
    extra: Optional[dict] = None,
) -> dict:
    """Build standard query params for /v1/* report endpoints.

    Note: company_id is intentionally excluded — it is not a documented
    parameter for any report endpoint and may cause empty results.
    """
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


def _unwrap(body: dict, row_key: str) -> list:
    """Extract paginated rows from BILLZ response.

    Some endpoints return {"rows": [...]} directly;
    others wrap in {"code": 200, "data": {"rows": [...]}}.
    """
    direct = body.get(row_key)
    if isinstance(direct, list):
        return direct
    # Try wrapped format: {"data": {"rows": [...]}} or {"data": [...]}
    data = body.get("data")
    if isinstance(data, dict):
        wrapped = data.get(row_key)
        if isinstance(wrapped, list):
            return wrapped
        # data itself might be the list (non-standard)
    if isinstance(data, list):
        return data
    return []


async def _paginate(endpoint: str, base_params: dict, row_key: str = "rows") -> list[dict]:
    """Fetch all pages from a paginated report endpoint."""
    from app.billz import client

    rows: list[dict] = []
    page = 1
    while True:
        params = {**base_params, "page": page}
        try:
            body = await client.get(endpoint, params=params)
        except Exception as exc:
            logger.error("BILLZ %s page=%d failed: %s", endpoint, page, exc)
            break

        page_rows = _unwrap(body, row_key)
        if not page_rows and page == 1:
            logger.warning("BILLZ %s: empty page 1, response keys=%s", endpoint, list(body.keys()))

        rows.extend(page_rows)
        limit = base_params.get("limit", 1000)
        if len(page_rows) < limit:
            break
        page += 1

    logger.info("BILLZ %s: got %d rows", endpoint, len(rows))
    return rows


# ---------------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------------

async def get_stock(report_date: str) -> list[dict]:
    """GET /v1/stock-report-table — stock levels as of report_date (YYYY-MM-DD).

    Key fields: product_name, product_sku, supplier_name, measurement_value,
    retail_price, supply_price, estimated_income, estimated_margin, categories_path.
    """
    params = _report_params(report_date=report_date, limit=1000)
    params.pop("start_date", None)
    params.pop("end_date", None)
    rows = await _paginate("/v1/stock-report-table", params)
    return rows


# ---------------------------------------------------------------------------
# Product sales
# ---------------------------------------------------------------------------

async def get_product_sales(start_date: str, end_date: str) -> list[dict]:
    """GET /v1/product-general-table — product sales for a date range.

    Key fields: product_name, product_sku, product_categories, product_brand_name,
    sold_measurement_value, returned_measurement_value, gross_sales,
    returned_sales_sum, net_profit, average_margin, discount.
    """
    params = _report_params(start_date=start_date, end_date=end_date, limit=1000)
    return await _paginate("/v1/product-general-table", params)


async def get_product_sales_summary(start_date: str, end_date: str) -> Optional[dict]:
    """GET /v1/product-general-report — aggregated product sales totals (no pagination).

    Returns a single object with summary metrics.
    """
    from app.billz import client
    params = _report_params(start_date=start_date, end_date=end_date, limit=1)
    try:
        body = await client.get("/v1/product-general-report", params=params)
        if isinstance(body.get("data"), dict):
            body = body["data"]
        return body
    except Exception as exc:
        logger.error("BILLZ product-general-report failed: %s", exc)
        return None


async def get_customer_purchases(
    start_date: str,
    end_date: str,
    with_customers: bool = False,
) -> list[dict]:
    """GET /v1/customer-purchases-table — purchases per product (alternative to product-general-table).

    Use with_customers=False to include all purchases regardless of customer linkage.
    NOTE: BILLZ API returns "puchases" key (missing 'r' — confirmed API typo in docs).

    Key fields: product_name, sold_measurement_value, gross_sales, net_profit, average_margin.
    """
    params = _report_params(
        start_date=start_date,
        end_date=end_date,
        limit=1000,
        extra={"with_customers": "false" if not with_customers else "true"},
    )
    return await _paginate("/v1/customer-purchases-table", params, row_key="puchases")


# ---------------------------------------------------------------------------
# General sales summary
# ---------------------------------------------------------------------------

async def get_summary(start_date: str, end_date: str) -> Optional[dict]:
    """GET /v1/general-report — period totals (not paginated).

    Key fields: gross_sales, net_gross_sales, gross_profit, average_cheque,
    products_sold, transactions_count, average_extra_charge.
    """
    from app.billz import client
    params = _report_params(start_date=start_date, end_date=end_date, limit=1)
    try:
        body = await client.get("/v1/general-report", params=params)
        # Unwrap {"code": 200, "data": {...}} if present
        if isinstance(body.get("data"), dict) and "gross_sales" not in body:
            body = body["data"]
        logger.debug("BILLZ general-report keys=%s gross_sales=%s",
                     list(body.keys()), body.get("gross_sales"))
        return body
    except Exception as exc:
        logger.error("BILLZ general-report failed: %s", exc)
        return None


async def get_general_table(
    start_date: str,
    end_date: str,
    detalization: str = "day",
) -> list[dict]:
    """GET /v1/general-report-table — sales broken down by day/week/month/year/all.

    detalization: "day" | "week" | "month" | "year" | "all"
    """
    params = _report_params(
        start_date=start_date,
        end_date=end_date,
        limit=1000,
        extra={"detalization": detalization},
    )
    return await _paginate("/v1/general-report-table", params)


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

async def get_transactions(start_date: str, end_date: str) -> list[dict]:
    """GET /v1/transaction-report-table — individual transaction details.

    Key fields: transaction_id, transaction_date, product_name, quantity,
    price, total, payment_type, shop_name, seller_name.
    """
    params = _report_params(start_date=start_date, end_date=end_date, limit=1000)
    params.pop("currency", None)  # not listed for this endpoint
    return await _paginate("/v1/transaction-report-table", params)


# ---------------------------------------------------------------------------
# Product performance (effectiveness)
# ---------------------------------------------------------------------------

async def get_product_performance(start_date: str, end_date: str) -> list[dict]:
    """GET /v1/report-product-performance-table — stock movement analysis.

    Shows beginning balance, received, sold, written-off, ending balance
    for each product. Useful for turnover analysis.

    Key fields: product_name, product_sku, opening_stock, closing_stock,
    received_qty, sold_qty, written_off_qty.
    """
    params = _report_params(start_date=start_date, end_date=end_date, limit=1000)
    return await _paginate("/v1/report-product-performance-table", params)


# ---------------------------------------------------------------------------
# Imports / purchases
# ---------------------------------------------------------------------------

async def get_imports(start_date: str, end_date: str) -> list[dict]:
    """GET /v1/import-report-table — product import records.

    Key fields: supplier_name, product_name, measurement_value, supply_price,
    total_price, import_date.
    """
    params = _report_params(
        start_date=start_date,
        end_date=end_date,
        limit=500,
        extra={"import_type": "import"},
    )
    return await _paginate("/v1/import-report-table", params)


async def get_supplier_sales(start_date: str, end_date: str) -> list[dict]:
    """GET /v1/product-sells-by-suppliers-table — sales breakdown by supplier.

    Key fields: supplier_name, product_name, sold_measurement_value,
    gross_sales, net_profit.
    """
    params = _report_params(start_date=start_date, end_date=end_date, limit=1000)
    return await _paginate("/v1/product-sells-by-suppliers-table", params)


async def get_order_returns(start_date: str, end_date: str) -> list[dict]:
    """GET /v1/supplier-order-return-report-table — returned supplier orders.

    Key fields: supplier_name, product_name, returned_qty, total_cost, return_date.
    """
    params = _report_params(start_date=start_date, end_date=end_date, limit=1000)
    return await _paginate("/v1/supplier-order-return-report-table", params)


# ---------------------------------------------------------------------------
# Stocktaking
# ---------------------------------------------------------------------------

async def get_stocktaking(start_date: str, end_date: str) -> list[dict]:
    """GET /v1/stocktaking-summary-table — inventory count results.

    Key fields: product_name, expected_qty, actual_qty, difference, shop_name.
    """
    params = _report_params(start_date=start_date, end_date=end_date, limit=1000)
    return await _paginate("/v1/stocktaking-summary-table", params)


# ---------------------------------------------------------------------------
# Write-offs
# ---------------------------------------------------------------------------

async def get_write_offs(start_date: str, end_date: str) -> list[dict]:
    """GET /v1/write-off-report-table — written-off products.

    NOTE: BILLZ returns "Items" key (capital I) instead of "rows".

    Key fields: product_name, product_sku, quantity, reason, write_off_date, shop_name.
    """
    params = _report_params(start_date=start_date, end_date=end_date, limit=1000)
    return await _paginate("/v1/write-off-report-table", params, row_key="Items")


# ---------------------------------------------------------------------------
# Sellers
# ---------------------------------------------------------------------------

async def get_seller_stats(start_date: str, end_date: str) -> list[dict]:
    """GET /v1/seller-general-table — seller performance metrics.

    Key fields: seller_name, gross_sales, net_profit, transactions_count,
    average_cheque, products_sold.
    """
    params = _report_params(start_date=start_date, end_date=end_date, limit=1000)
    return await _paginate("/v1/seller-general-table", params)


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

async def get_customer_stats(start_date: str, end_date: str) -> list[dict]:
    """GET /v1/customer-general-table — customer analytics.

    Key fields: customer_name, customer_phone, orders_count, total_spent,
    average_cheque, last_order_date.
    """
    params = _report_params(start_date=start_date, end_date=end_date, limit=1000)
    return await _paginate("/v1/customer-general-table", params)


# ---------------------------------------------------------------------------
# Legacy stub
# ---------------------------------------------------------------------------

async def get_promos() -> Optional[list[dict]]:
    """Promo/discount list — not available in BILLZ report API (Отчеты.pdf).

    The digest promo block is driven by AI analysis of stock + sales data.
    """
    return None
