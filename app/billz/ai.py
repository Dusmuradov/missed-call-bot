"""
AI-анализ KPI-данных BILLZ через DeepSeek (OpenAI SDK, клиент из hermes/llm.py).
Возвращает строгий 3-блочный JSON: закуп, продажи, акции.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_PROMO_TYPES = (
    "Типы акций (используй именно эти механики в поле 'mechanics'):\n"
    "1. Скидка — прямая скидка % на товар или категорию\n"
    "2. Bundle — комбо/набор (2 по цене 1.5 и т.п.)\n"
    "3. Подарок — подарок при покупке основного товара\n"
    "4. Loyalty — кешбэк / начисление бонусов лояльности\n"
    "5. Флэш — ограниченная по времени скидка (1–3 дня)\n"
    "6. Liquidation — распродажа аутсайдеров (группа C ABC)"
)

_SYSTEM = f"""Ты — эксперт-аналитик розничного POS-магазина Neoclassica (мебель, Узбекистан).
Анализируй данные продаж и давай конкретные, actionable советы на русском языке.
Цифры в сумах (UZS).

{_PROMO_TYPES}

ВАЖНО: верни ТОЛЬКО валидный JSON без markdown-обёртки, без пояснений. Формат строго:
{{
  "zakup": {{
    "summary": "1-2 предложения о состоянии закупа",
    "actions": ["конкретное действие 1", "конкретное действие 2"]
  }},
  "prodaji": {{
    "summary": "1-2 предложения о продажах за период",
    "highlights": ["позитивная находка 1", "позитивная находка 2"],
    "warnings": ["тревожный сигнал 1"],
    "comparison": "описание WoW/DoD динамики одной строкой"
  }},
  "akcii": {{
    "recommendations": [
      {{
        "title": "название акции",
        "mechanics": "тип (одно из: Скидка/Bundle/Подарок/Loyalty/Флэш/Liquidation)",
        "target_products": ["товар1", "товар2"],
        "expected_effect": "ожидаемый результат"
      }}
    ]
  }}
}}"""


def _build_prompt(kpi: dict) -> str:
    """Формирует текстовый промпт из KPI dict."""
    lines = [
        f"ПЕРИОД: {kpi.get('period', '—')}",
        f"ВЫРУЧКА: {kpi.get('revenue', 0):,.0f} сум",
        f"ЗАКАЗОВ: {kpi.get('orders', 0)} | СР. ЧЕК: {kpi.get('aov', 0):,.0f} сум",
        f"ТОВАРОВ ПРОДАНО: {kpi.get('items_sold', 0):,.0f} ед",
        f"НАЛИЧНЫЕ: {kpi.get('cash', 0):,.0f} | КАРТА: {kpi.get('card', 0):,.0f}",
    ]

    # Динамика WoW/DoD
    cmp = kpi.get("comparison") or {}
    if cmp.get("wow_pct") is not None:
        sign = "▲" if cmp["wow_pct"] >= 0 else "▼"
        lines.append(
            f"ДИНАМИКА: {sign}{abs(cmp['wow_pct']):.1f}% vs пред. период"
            f" (было: {cmp.get('prev_revenue', 0):,.0f} сум, {cmp.get('prev_orders', '—')} заказов)"
        )
    else:
        lines.append("ДИНАМИКА: нет данных за предыдущий период")

    # Топ-10 товаров
    top = kpi.get("top10") or []
    if top:
        lines.append("\nТОП-10 ТОВАРОВ (по выручке):")
        for i, p in enumerate(top[:10], 1):
            lines.append(f"  {i}. {p['name']}: {p['revenue']:,.0f} сум ({p['qty']:.0f} ед)")

    # Аутсайдеры группы C
    abc = kpi.get("abc") or {}
    c_group = (abc.get("C") or [])[:15]
    if c_group:
        lines.append(f"\nАУТСАЙДЕРЫ группа C ({len(abc.get('C', []))} товаров, показаны первые):")
        lines.append("  " + ", ".join(c_group[:12]))

    # Топ категорий
    cats = kpi.get("categories") or {}
    if cats:
        lines.append("\nТОП КАТЕГОРИЙ:")
        for cat, rev in list(cats.items())[:6]:
            lines.append(f"  {cat}: {rev:,.0f} сум")

    # Скорость продаж топ-15 (для закупа)
    velocity = kpi.get("velocity") or {}
    if velocity:
        lines.append("\nСКОРОСТЬ ПРОДАЖ за период (ед/период, топ-15 для закупа):")
        for name, qty in list(velocity.items())[:15]:
            lines.append(f"  {name}: {qty:.0f} ед")

    # Остатки (если доступны)
    stock = kpi.get("stock") or {}
    if stock:
        stockout = stock.get("stockout_risk") or []
        low = stock.get("low_stock") or []
        overstock = stock.get("overstock") or []
        if stockout:
            lines.append(f"\n🔴 СРОЧНЫЙ ДОЗАКАЗ (DoS < 7 дней, {len(stockout)} товаров):")
            for item in stockout[:8]:
                dos = f"{item['days_of_supply']} дн" if item.get("days_of_supply") is not None else "∞"
                lines.append(f"  {item['name']} (у поставщика: {item['supplier']}): остаток {item['qty']} ед, DoS={dos}")
        if low:
            lines.append(f"\n🟡 ПОПОЛНИТЬ В БЛИЖАЙШИЕ ДНИ (DoS 7–14 дней, {len(low)} товаров):")
            for item in low[:5]:
                dos = f"{item['days_of_supply']} дн" if item.get("days_of_supply") is not None else "∞"
                lines.append(f"  {item['name']}: остаток {item['qty']} ед, DoS={dos}")
        if overstock:
            lines.append(f"\n⚪ ЗАВИСШИЕ ТОВАРЫ (0 продаж за период, {len(overstock)} товаров):")
            for item in overstock[:5]:
                lines.append(f"  {item['name']}: {item['qty']} ед, поставщик: {item['supplier']}")
        if stock.get("total_retail_value"):
            lines.append(f"\nОБЩАЯ СТОИМОСТЬ СКЛАДА (розн.): {stock['total_retail_value']:,.0f} сум")

    # Маржинальность по товарам (из product-general-table)
    ps = kpi.get("product_sales") or {}
    if ps:
        if ps.get("total_net_profit"):
            lines.append(
                f"\nВАЛОВАЯ ПРИБЫЛЬ: {ps['total_net_profit']:,.0f} сум"
                f"  (выручка: {ps.get('total_gross_sales', 0):,.0f} сум)"
            )
        top_profit = ps.get("top_by_profit") or []
        if top_profit:
            lines.append("\nТОП ТОВАРЫ ПО ЧИСТОЙ ПРИБЫЛИ:")
            for p in top_profit[:6]:
                lines.append(
                    f"  {p['name']}: прибыль {p['net_profit']:,.0f}, маржа {p['margin_pct']}%"
                )
        low_margin = ps.get("low_margin") or []
        if low_margin:
            lines.append(
                f"\nНИЗКАЯ МАРЖА <15% (кандидаты на работу с ценой, {len(low_margin)} товаров):"
            )
            for p in low_margin[:5]:
                lines.append(
                    f"  {p['name']}: маржа {p['margin_pct']}%, выручка {p['gross_sales']:,.0f}"
                )
        high_returns = ps.get("high_return_rate") or []
        if high_returns:
            lines.append("\nВЫСОКИЙ % ВОЗВРАТОВ (требуют внимания):")
            for p in high_returns[:3]:
                lines.append(f"  {p['name']}: {p['return_rate']}% возвратов ({p['returned_qty']:.0f} из {p['sold_qty']:.0f} ед)")

    # Импорты — история поставок (из import-report-table)
    imp = kpi.get("imports") or {}
    if imp:
        if imp.get("total_cost"):
            lines.append(f"\nЗАКУПЛЕНО ЗА ПЕРИОД: {imp['total_cost']:,.0f} сум")
        by_sup = imp.get("by_supplier") or {}
        if by_sup:
            lines.append("ПО ПОСТАВЩИКАМ (сумма поступлений):")
            for supplier, cost in list(by_sup.items())[:5]:
                lines.append(f"  {supplier}: {cost:,.0f} сум")
        recent = imp.get("recent") or []
        if recent:
            lines.append("ПОСЛЕДНИЕ ПОСТУПЛЕНИЯ:")
            for item in recent[:5]:
                lines.append(
                    f"  {item['product']} от {item['supplier']}: "
                    f"{item['qty']} ед, {item['date']}"
                )

    # Продажи по поставщикам (из product-sells-by-suppliers-table)
    sup_sales = kpi.get("supplier_sales") or {}
    by_supplier = sup_sales.get("by_supplier") or []
    if by_supplier:
        lines.append("\nРЕЙТИНГ ПОСТАВЩИКОВ ПО ПРИБЫЛИ:")
        for s in by_supplier[:5]:
            lines.append(
                f"  {s['supplier']}: прибыль {s['net_profit']:,.0f}, маржа {s['margin_pct']}%"
            )

    # Сводный отчёт BILLZ (верификация)
    summary = kpi.get("summary") or {}
    if summary:
        gp = summary.get("gross_profit") or summary.get("net_gross_sales") or 0
        if gp:
            lines.append(f"\nBILLZ СВОДНЫЙ — валовая прибыль: {float(gp):,.0f} сум")

    # Канальная атрибуция
    channels = kpi.get("channels") or {}
    cc = channels.get("call_center") or {}
    qf = channels.get("qayerdan") or {}
    if cc:
        lines.append("\nКОЛЛ-ЦЕНТР (заказов):")
        for op, cnt in list(cc.items())[:5]:
            lines.append(f"  {op}: {cnt}")
    if qf:
        lines.append("\nОТКУДА КЛИЕНТ:")
        for src, cnt in list(qf.items())[:5]:
            lines.append(f"  {src}: {cnt}")

    return "\n".join(lines)


async def analyze(kpi: dict) -> dict:
    """
    Прогоняет KPI через DeepSeek и возвращает 3-блочный dict.
    При ошибке парсинга или недоступности AI — возвращает fallback.
    """
    try:
        from hermes.llm import get_llm_client
        llm = get_llm_client()
    except RuntimeError as exc:
        logger.warning("BILLZ AI skipped (LLM not configured): %s", exc)
        return _fallback(str(exc))

    prompt = _build_prompt(kpi)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": prompt},
    ]

    try:
        result = await llm.chat(messages, temperature=0.3, max_tokens=1600)
        content = (result.get("content") or "").strip()

        # Убираем markdown-обёртку на случай если модель её добавила
        if content.startswith("```"):
            parts = content.split("```")
            content = parts[1] if len(parts) > 1 else content
            if content.startswith("json"):
                content = content[4:].strip()

        parsed = json.loads(content)
        logger.info("BILLZ AI analysis complete")
        return parsed

    except json.JSONDecodeError as exc:
        logger.error("BILLZ AI: JSON parse failed: %s | content=%s", exc, content[:200])
        return _fallback("AI вернул невалидный JSON")
    except Exception as exc:
        logger.error("BILLZ AI: request failed: %s", exc)
        return _fallback(str(exc))


def _fallback(reason: str) -> dict:
    return {
        "zakup": {
            "summary": f"⚠️ AI-анализ недоступен: {reason}",
            "actions": [],
        },
        "prodaji": {
            "summary": "Данные получены, AI-интерпретация недоступна.",
            "highlights": [],
            "warnings": [],
            "comparison": "—",
        },
        "akcii": {
            "recommendations": [],
        },
    }
