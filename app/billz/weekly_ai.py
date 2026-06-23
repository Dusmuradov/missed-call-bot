"""
AI-анализ недельного дайджеста: BILLZ + AmoCRM + Utel → рекомендации на следующую неделю.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_SYSTEM = """Ты — бизнес-аналитик розничного мебельного магазина Neoclassica (Узбекистан).
Каждый понедельник ты получаешь сводку за прошлую неделю: продажи, остатки, лиды, звонки.
Твоя задача — дать конкретный план действий на текущую неделю.
Цифры в сумах (UZS). Отвечай строго на русском языке.

ВАЖНО: верни ТОЛЬКО валидный JSON без markdown-обёртки. Формат строго:
{
  "week_summary": "2-3 предложения — главное за неделю, тренд",
  "sales": {
    "highlights": ["позитив 1", "позитив 2"],
    "warnings": ["тревога 1", "тревога 2"]
  },
  "stock": {
    "urgent_reorder": ["товар1 — X ед осталось", "товар2 — Y ед"],
    "actions": ["конкретное действие по закупу 1", "действие 2"]
  },
  "crm": {
    "top_performer": "имя и результат",
    "needs_attention": "кто отстаёт и почему",
    "actions": ["что сделать с командой продаж"]
  },
  "calls": {
    "summary": "краткий вывод по звонкам",
    "actions": ["действие по колл-центру если нужно"]
  },
  "next_week_plan": [
    "1. конкретное действие",
    "2. конкретное действие",
    "3. конкретное действие",
    "4. конкретное действие",
    "5. конкретное действие"
  ],
  "promo": {
    "title": "название акции на неделю",
    "mechanics": "Скидка/Bundle/Подарок/Loyalty/Флэш/Liquidation",
    "target": "на какой товар/категорию",
    "reason": "почему именно это"
  }
}"""


def _build_prompt(
    period_label: str,
    billz_kpi: dict | None,
    amo_data: dict | None,
    utel_stats: dict | None,
) -> str:
    lines = [f"ОТЧЁТНЫЙ ПЕРИОД: {period_label}\n"]

    # ── BILLZ продажи ──
    if billz_kpi:
        lines.append("=== ПРОДАЖИ (BILLZ POS) ===")
        lines.append(f"Выручка: {billz_kpi.get('revenue', 0):,.0f} сум")
        lines.append(f"Заказов: {billz_kpi.get('orders', 0)} | Средний чек: {billz_kpi.get('aov', 0):,.0f} сум")
        lines.append(f"Товаров продано: {billz_kpi.get('items_sold', 0):,.0f} ед")

        cmp = billz_kpi.get("comparison") or {}
        if cmp.get("wow_pct") is not None:
            sign = "▲" if cmp["wow_pct"] >= 0 else "▼"
            lines.append(
                f"Динамика WoW: {sign}{abs(cmp['wow_pct']):.1f}% "
                f"(было: {cmp.get('prev_revenue', 0):,.0f} сум, {cmp.get('prev_orders', '—')} заказов)"
            )

        top10 = billz_kpi.get("top10") or []
        if top10:
            lines.append("\nТоп-10 товаров по выручке:")
            for i, p in enumerate(top10[:10], 1):
                lines.append(f"  {i}. {p['name']}: {p['revenue']:,.0f} сум ({p['qty']:.0f} ед)")

        ps = billz_kpi.get("product_sales") or {}
        if ps.get("total_net_profit"):
            lines.append(f"\nВаловая прибыль: {ps['total_net_profit']:,.0f} сум")
        low_margin = ps.get("low_margin") or []
        if low_margin:
            lines.append(f"Товары с низкой маржой (<15%, {len(low_margin)} шт):")
            for p in low_margin[:4]:
                lines.append(f"  {p['name']}: маржа {p['margin_pct']}%")

        # Остатки
        stock = billz_kpi.get("stock") or {}
        if stock:
            lines.append("\n=== ОСТАТКИ ===")
            stockout = stock.get("stockout_risk") or []
            if stockout:
                lines.append(f"🔴 СРОЧНЫЙ ДОЗАКАЗ (DoS < 7 дней, {len(stockout)} товаров):")
                for item in stockout[:8]:
                    dos = f"{item['days_of_supply']} дн" if item.get("days_of_supply") is not None else "∞"
                    lines.append(f"  {item['name']} (поставщик: {item['supplier']}): {item['qty']} ед, DoS={dos}")
            low = stock.get("low_stock") or []
            if low:
                lines.append(f"🟡 Пополнить (DoS 7–14 дней, {len(low)} товаров):")
                for item in low[:5]:
                    lines.append(f"  {item['name']}: {item['qty']} ед")
            overstock = stock.get("overstock") or []
            if overstock:
                lines.append(f"⚪ Зависшие товары (0 продаж, {len(overstock)} шт):")
                for item in overstock[:5]:
                    lines.append(f"  {item['name']}: {item['qty']} ед")
            if stock.get("total_retail_value"):
                lines.append(f"Стоимость склада (розн.): {stock['total_retail_value']:,.0f} сум")

        imp = billz_kpi.get("imports") or {}
        if imp.get("total_cost"):
            lines.append(f"\nЗакуплено за неделю: {imp['total_cost']:,.0f} сум")

    # ── AmoCRM лиды ──
    if amo_data and not amo_data.get("error"):
        lines.append("\n=== ЛИДЫ (AmoCRM) ===")
        lines.append(f"Всего лидов за неделю: {amo_data.get('total_leads', 0)}")
        users = amo_data.get("users") or {}
        if users:
            lines.append("По менеджерам:")
            for uid, u in sorted(users.items(), key=lambda x: x[1].get("total", 0), reverse=True):
                lines.append(
                    f"  {u['name']}: {u['total']} лидов | "
                    f"обработано: {u['processed']} | конверсия: {u['conversion_rate']}%"
                )

    # ── Utel звонки ──
    if utel_stats:
        lines.append("\n=== ЗВОНКИ (Utel) ===")
        lines.append(f"Всего звонков: {utel_stats.get('total', 0)}")
        lines.append(f"Входящих: {utel_stats.get('incoming', 0)} | Пропущенных: {utel_stats.get('missed', 0)}")
        lines.append(f"Исходящих: {utel_stats.get('outgoing', 0)}")
        miss_rate = utel_stats.get("missed_rate")
        if miss_rate is not None:
            lines.append(f"% пропущенных: {miss_rate:.1f}%")

    return "\n".join(lines)


async def analyze_weekly(
    period_label: str,
    billz_kpi: dict | None,
    amo_data: dict | None,
    utel_stats: dict | None,
) -> dict:
    """Прогоняет данные через DeepSeek и возвращает недельный план."""
    try:
        from hermes.llm import get_llm_client
        llm = get_llm_client()
    except RuntimeError as exc:
        logger.warning("Weekly AI skipped (LLM not configured): %s", exc)
        return _fallback(str(exc))

    prompt = _build_prompt(period_label, billz_kpi, amo_data, utel_stats)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": prompt},
    ]

    try:
        result = await llm.chat(messages, temperature=0.4, max_tokens=2000)
        content = (result.get("content") or "").strip()

        if content.startswith("```"):
            parts = content.split("```")
            content = parts[1] if len(parts) > 1 else content
            if content.startswith("json"):
                content = content[4:].strip()

        parsed = json.loads(content)
        logger.info("Weekly AI analysis complete")
        return parsed

    except json.JSONDecodeError as exc:
        logger.error("Weekly AI: JSON parse failed: %s", exc)
        return _fallback("AI вернул невалидный JSON")
    except Exception as exc:
        logger.error("Weekly AI: request failed: %s", exc)
        return _fallback(str(exc))


def _fallback(reason: str) -> dict:
    return {
        "week_summary": f"⚠️ AI-анализ недоступен: {reason}",
        "sales": {"highlights": [], "warnings": []},
        "stock": {"urgent_reorder": [], "actions": []},
        "crm": {"top_performer": "—", "needs_attention": "—", "actions": []},
        "calls": {"summary": "—", "actions": []},
        "next_week_plan": [],
        "promo": None,
    }


def format_weekly_digest(ai: dict, period_label: str) -> list[str]:
    """Форматирует AI-ответ в Telegram-сообщения (HTML)."""
    messages = []

    # ── Сообщение 1: Сводка + Продажи + CRM ──
    lines = [f"📅 <b>Недельный дайджест — {period_label}</b>\n"]

    summary = ai.get("week_summary", "")
    if summary:
        lines.append(f"<i>{summary}</i>\n")

    sales = ai.get("sales") or {}
    if sales.get("highlights") or sales.get("warnings"):
        lines.append("📊 <b>Продажи</b>")
        for h in (sales.get("highlights") or []):
            lines.append(f"✅ {h}")
        for w in (sales.get("warnings") or []):
            lines.append(f"⚠️ {w}")
        lines.append("")

    crm = ai.get("crm") or {}
    if crm.get("top_performer") or crm.get("needs_attention"):
        lines.append("👥 <b>Команда продаж</b>")
        if crm.get("top_performer"):
            lines.append(f"🏆 Лучший: {crm['top_performer']}")
        if crm.get("needs_attention"):
            lines.append(f"📉 Требует внимания: {crm['needs_attention']}")
        for a in (crm.get("actions") or []):
            lines.append(f"→ {a}")
        lines.append("")

    calls = ai.get("calls") or {}
    if calls.get("summary"):
        lines.append("📞 <b>Звонки</b>")
        lines.append(calls["summary"])
        for a in (calls.get("actions") or []):
            lines.append(f"→ {a}")

    messages.append("\n".join(lines))

    # ── Сообщение 2: Остатки + План на неделю + Акция ──
    lines2 = []

    stock = ai.get("stock") or {}
    if stock.get("urgent_reorder") or stock.get("actions"):
        lines2.append("📦 <b>Остатки и закуп</b>")
        for item in (stock.get("urgent_reorder") or []):
            lines2.append(f"🔴 {item}")
        for a in (stock.get("actions") or []):
            lines2.append(f"→ {a}")
        lines2.append("")

    plan = ai.get("next_week_plan") or []
    if plan:
        lines2.append("📋 <b>План на текущую неделю</b>")
        for p in plan:
            lines2.append(p)
        lines2.append("")

    promo = ai.get("promo")
    if promo:
        lines2.append("🎯 <b>Акция недели</b>")
        lines2.append(f"<b>{promo.get('title', '')}</b> ({promo.get('mechanics', '')})")
        lines2.append(f"Цель: {promo.get('target', '')}")
        lines2.append(f"Причина: {promo.get('reason', '')}")

    if lines2:
        messages.append("\n".join(lines2))

    return messages
