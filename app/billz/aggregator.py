"""
KPI-агрегатор BILLZ — чистый Python без I/O.
Принимает список нормализованных detalей заказов (из reports.parse_order_detail),
возвращает dict со всеми метриками для форматтера и AI.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional


def aggregate(
    order_details: list[dict],
    period_label: str = "Вчера",
    prev_snapshot: Optional[dict] = None,
) -> dict:
    """
    Считает KPI по списку dicts из reports.parse_order_detail().

    Метрики:
    - Выручка, кол-во заказов, средний чек (AOV)
    - Разбивка по методу оплаты (наличные/карта)
    - Топ-10 / аутсайдеры по выручке
    - Разбивка по категориям
    - ABC-анализ товаров (A=80%, B=15%, C=5%)
    - Скорость продаж (qty за период — для советов по закупу)
    - Канальная атрибуция (call_center / qayerdan keldi)
    - WoW/DoD сравнение с prev_snapshot
    """
    if not order_details:
        return _empty(period_label, prev_snapshot)

    revenue = sum(o["total"] for o in order_details)
    orders_count = len(order_details)
    aov = revenue / orders_count if orders_count else 0.0
    items_sold = sum(o["total_qty"] for o in order_details)
    cash = sum(o["cash"] for o in order_details)
    card = sum(o["card"] for o in order_details)

    # Агрегация по товарам и категориям
    products: dict[str, dict] = defaultdict(lambda: {"revenue": 0.0, "qty": 0.0})
    categories: dict[str, float] = defaultdict(float)

    for od in order_details:
        for item in (od.get("items") or []):
            name = item.get("name") or "—"
            cat = item.get("category") or "—"
            price = float(item.get("price") or 0)
            qty = float(item.get("qty") or 0)
            item_revenue = price * qty
            products[name]["revenue"] += item_revenue
            products[name]["qty"] += qty
            categories[cat] += item_revenue

    # Сортировка по убыванию выручки
    sorted_products = sorted(products.items(), key=lambda x: x[1]["revenue"], reverse=True)

    top10 = [{"name": n, "revenue": round(v["revenue"], 2), "qty": round(v["qty"], 2)}
             for n, v in sorted_products[:10]]

    # Аутсайдеры — товары с продажами, но минимальной выручкой
    bottom10 = [{"name": n, "revenue": round(v["revenue"], 2), "qty": round(v["qty"], 2)}
                for n, v in sorted_products if v["revenue"] > 0][-10:]

    abc = _abc_analysis(sorted_products, revenue)

    # Скорость продаж (qty/период) — топ-50 для советов по закупу
    velocity = {name: round(v["qty"], 2) for name, v in sorted_products[:50]}

    # Канальная атрибуция
    cc_stats: dict[str, int] = defaultdict(int)
    qf_stats: dict[str, int] = defaultdict(int)
    for od in order_details:
        if od.get("call_center"):
            cc_stats[od["call_center"]] += 1
        if od.get("qayerdan"):
            qf_stats[od["qayerdan"]] += 1

    return {
        "period": period_label,
        "revenue": round(revenue, 2),
        "orders": orders_count,
        "aov": round(aov, 2),
        "items_sold": round(items_sold, 2),
        "cash": round(cash, 2),
        "card": round(card, 2),
        "categories": dict(sorted(categories.items(), key=lambda x: x[1], reverse=True)),
        "top10": top10,
        "bottom10": list(reversed(bottom10))[:10],
        "abc": abc,
        "velocity": velocity,
        "channels": {
            "call_center": dict(sorted(cc_stats.items(), key=lambda x: x[1], reverse=True)),
            "qayerdan": dict(sorted(qf_stats.items(), key=lambda x: x[1], reverse=True)),
        },
        "comparison": _comparison(revenue, orders_count, aov, prev_snapshot),
    }


def _abc_analysis(sorted_products: list, total_revenue: float) -> dict[str, list[str]]:
    """
    ABC-анализ: A — 80% выручки, B — следующие 15%, C — остаток 5%.
    Группа C = кандидаты на акцию/liquidation.
    """
    if total_revenue == 0:
        return {"A": [], "B": [], "C": []}

    a_names: list[str] = []
    b_names: list[str] = []
    c_names: list[str] = []
    cumulative = 0.0

    for name, v in sorted_products:
        share = cumulative / total_revenue
        if share < 0.80:
            a_names.append(name)
        elif share < 0.95:
            b_names.append(name)
        else:
            c_names.append(name)
        cumulative += v["revenue"]

    return {"A": a_names, "B": b_names, "C": c_names}


def _comparison(
    revenue: float,
    orders: int,
    aov: float,
    prev: Optional[dict],
) -> dict:
    """Считает WoW/DoD динамику относительно prev_snapshot."""
    if not prev or prev.get("revenue", 0) == 0:
        return {
            "wow_pct": None,
            "prev_revenue": prev.get("revenue") if prev else None,
            "prev_orders": prev.get("orders") if prev else None,
            "prev_aov": prev.get("aov") if prev else None,
        }
    prev_rev = prev["revenue"]
    pct = (revenue - prev_rev) / prev_rev * 100
    return {
        "wow_pct": round(pct, 1),
        "prev_revenue": round(prev_rev, 2),
        "prev_orders": prev.get("orders"),
        "prev_aov": prev.get("aov"),
    }


def _empty(period_label: str, prev_snapshot: Optional[dict]) -> dict:
    return {
        "period": period_label,
        "revenue": 0,
        "orders": 0,
        "aov": 0,
        "items_sold": 0,
        "cash": 0,
        "card": 0,
        "categories": {},
        "top10": [],
        "bottom10": [],
        "abc": {"A": [], "B": [], "C": []},
        "velocity": {},
        "channels": {"call_center": {}, "qayerdan": {}},
        "comparison": _comparison(0, 0, 0, prev_snapshot),
    }


def snapshot_from_kpi(kpi: dict) -> dict:
    """Извлекает только те поля, которые сохраняются в BillzSnapshot для сравнений."""
    return {
        "revenue": kpi.get("revenue", 0),
        "orders": kpi.get("orders", 0),
        "aov": kpi.get("aov", 0),
        "items_sold": kpi.get("items_sold", 0),
    }


def aggregate_product_sales(rows: list[dict]) -> dict:
    """
    Агрегирует маржинальность из /v1/product-general-table.
    Возвращает топ по прибыли, товары с низкой маржой и высоким % возвратов.
    """
    if not rows:
        return {"top_by_profit": [], "low_margin": [], "high_return_rate": [],
                "total_net_profit": 0, "total_gross_sales": 0}

    products = []
    for row in rows:
        name = row.get("product_name") or "—"
        sku = row.get("product_sku") or ""
        cats = row.get("product_categories") or []
        if isinstance(cats, list):
            categories = ", ".join(str(c) for c in cats) if cats else "—"
        else:
            categories = str(cats) if cats else "—"
        gross = float(row.get("gross_sales") or 0)
        profit = float(row.get("net_profit") or 0)
        margin = float(row.get("average_margin") or 0)
        sold = float(row.get("sold_measurement_value") or 0)
        returned = float(row.get("returned_measurement_value") or 0)
        return_rate = round(returned / sold * 100, 1) if sold > 0 else 0.0
        supply_cost = round(gross - profit, 2)
        products.append({
            "name": name,
            "sku": sku,
            "categories": categories,
            "gross_sales": round(gross, 2),
            "net_profit": round(profit, 2),
            "supply_cost": supply_cost,
            "margin_pct": round(margin, 1),
            "sold_qty": round(sold, 2),
            "returned_qty": round(returned, 2),
            "return_rate": return_rate,
        })

    by_profit = sorted(products, key=lambda x: x["net_profit"], reverse=True)
    # Товары с продажами но низкой маржой — кандидаты на пересмотр цены или акцию
    low_margin = sorted(
        [p for p in products if 0 < p["margin_pct"] < 15 and p["gross_sales"] > 0],
        key=lambda x: x["margin_pct"],
    )
    high_returns = sorted(
        [p for p in products if p["return_rate"] > 10 and p["sold_qty"] > 0],
        key=lambda x: x["return_rate"], reverse=True,
    )

    return {
        "top_by_profit": by_profit[:10],
        "low_margin": low_margin[:10],
        "high_return_rate": high_returns[:5],
        "total_net_profit": round(sum(p["net_profit"] for p in products), 2),
        "total_gross_sales": round(sum(p["gross_sales"] for p in products), 2),
    }


def aggregate_imports(rows: list[dict]) -> dict:
    """
    Агрегирует поступления товаров из /v1/import-report-table.
    Возвращает последние поступления и разбивку по поставщикам.
    """
    if not rows:
        return {"recent": [], "by_supplier": {}, "total_cost": 0}

    by_supplier: dict[str, float] = defaultdict(float)
    recent = []
    total_cost = 0.0

    for row in rows:
        supplier = row.get("supplier_name") or "—"
        product = row.get("product_name") or "—"
        qty = float(row.get("measurement_value") or row.get("quantity") or 0)
        unit_cost = float(row.get("supply_price") or row.get("price") or 0)
        total = float(row.get("total_price") or 0) or (qty * unit_cost)
        date = row.get("import_date") or row.get("created_at") or ""

        by_supplier[supplier] += total
        total_cost += total
        recent.append({
            "supplier": supplier,
            "product": product,
            "qty": round(qty, 2),
            "total": round(total, 2),
            "date": str(date)[:10] if date else "—",
        })

    recent.sort(key=lambda x: x["date"], reverse=True)

    return {
        "recent": recent[:15],
        "by_supplier": dict(sorted(by_supplier.items(), key=lambda x: x[1], reverse=True)),
        "total_cost": round(total_cost, 2),
    }


def aggregate_supplier_sales(rows: list[dict]) -> dict:
    """
    Агрегирует продажи по поставщикам из /v1/product-sells-by-suppliers-table.
    Возвращает рейтинг поставщиков по прибыли.
    """
    if not rows:
        return {"by_supplier": []}

    suppliers: dict[str, dict] = defaultdict(
        lambda: {"gross_sales": 0.0, "net_profit": 0.0, "sold_qty": 0.0}
    )
    for row in rows:
        supplier = row.get("supplier_name") or "—"
        suppliers[supplier]["gross_sales"] += float(row.get("gross_sales") or 0)
        suppliers[supplier]["net_profit"] += float(row.get("net_profit") or 0)
        suppliers[supplier]["sold_qty"] += float(row.get("sold_measurement_value") or 0)

    result = [
        {
            "supplier": name,
            "gross_sales": round(v["gross_sales"], 2),
            "net_profit": round(v["net_profit"], 2),
            "margin_pct": round(
                v["net_profit"] / v["gross_sales"] * 100, 1
            ) if v["gross_sales"] > 0 else 0.0,
            "sold_qty": round(v["sold_qty"], 2),
        }
        for name, v in suppliers.items()
    ]
    result.sort(key=lambda x: x["net_profit"], reverse=True)
    return {"by_supplier": result}


def aggregate_stock(stock_rows: list[dict], velocity: dict[str, float]) -> dict:
    """
    Агрегирует данные остатков из /v1/stock-report-table.

    Для каждого товара вычисляет Days of Supply (DoS):
      DoS = measurement_value / velocity_per_day
    Если velocity == 0 → DoS = infinity (зависший товар).

    Возвращает dict с категориями:
    - stockout_risk: товары с DoS < 7 дней (срочный заказ)
    - low_stock: товары с DoS 7–14 дней
    - overstock: товары с нулевыми продажами за период
    - total_retail_value: общая розничная стоимость склада
    """
    if not stock_rows:
        return {
            "stockout_risk": [],
            "low_stock": [],
            "overstock": [],
            "total_retail_value": 0,
        }

    stockout_risk = []
    low_stock = []
    overstock = []
    total_retail = 0.0

    for row in stock_rows:
        name = row.get("product_name") or "—"
        qty = float(row.get("measurement_value") or 0)
        retail_price = float(row.get("retail_price") or 0)
        supply_price = float(row.get("supply_price") or 0)
        supplier = row.get("supplier_name") or "—"
        sku = row.get("product_sku") or ""
        category = row.get("categories_path") or "—"
        estimated_income = float(row.get("estimated_income") or 0)
        estimated_margin = float(row.get("estimated_margin") or 0)

        retail_value = round(qty * retail_price, 2)
        cost_value = round(qty * supply_price, 2)
        total_retail += retail_value

        vel = velocity.get(name, 0.0)  # ед/день (уже нормализовано вызывающим кодом)
        dos = (qty / vel) if vel > 0 else float("inf")

        item = {
            "name": name,
            "sku": sku,
            "supplier": supplier,
            "category": category,
            "qty": round(qty, 2),
            "retail_price": retail_price,
            "supply_price": supply_price,
            "retail_value": retail_value,
            "cost_value": cost_value,
            "estimated_income": round(estimated_income, 2),
            "estimated_margin": round(estimated_margin, 2),
            "velocity_per_day": round(vel, 2),
            "days_of_supply": round(dos, 1) if dos != float("inf") else None,
        }

        if vel == 0:
            overstock.append(item)
        elif dos < 7:
            stockout_risk.append(item)
        elif dos < 14:
            low_stock.append(item)

    # Сортируем по срочности
    stockout_risk.sort(key=lambda x: x.get("days_of_supply") or 0)
    low_stock.sort(key=lambda x: x.get("days_of_supply") or 0)

    return {
        "stockout_risk": stockout_risk[:15],    # топ-15 самых срочных
        "low_stock": low_stock[:10],
        "overstock": overstock[:10],
        "total_retail_value": round(total_retail, 2),
    }
