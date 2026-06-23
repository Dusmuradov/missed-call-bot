"""
Stage 2: Daily prioritized task plan (P1/P2/P3) for a single seller.
"""
from __future__ import annotations

import asyncio
import logging
from html import escape

from hermes.audit import DealAnalysis, run_audit
from hermes.rop_errors import DealError, detect_errors

logger = logging.getLogger(__name__)

_MAX_P1 = 5
_MAX_P2 = 5
_MAX_P3 = 3
_HIGH_VALUE = 500_000


async def build_daily_plan(amocrm_user_id: int, tg_user_id: int) -> str:
    """Return a Telegram HTML string with the seller's P1/P2/P3 plan for today."""
    from app.amocrm.client import get_valid_client
    from hermes.skills.loader import load_skills

    load_skills()
    context: dict = {}
    try:
        from hermes.llm import get_llm_client
        context = {
            "llm": get_llm_client(),
            "amocrm_user_id": amocrm_user_id,
            "tg_user_id": tg_user_id,
        }
    except RuntimeError:
        pass

    deals = await run_audit(amocrm_user_id, tg_user_id, with_suggestions=True)
    if not deals:
        return (
            "📋 <b>Твой план на сегодня</b>\n\n"
            "Активных сделок нет — хорошее время проработать базу и найти новых клиентов."
        )

    client = await get_valid_client()

    async def _enrich(deal: DealAnalysis):
        tasks, notes = [], []
        if client:
            try:
                t, n = await asyncio.gather(
                    client.get_lead_tasks(deal.lead_id),
                    client.get_lead_notes(deal.lead_id),
                    return_exceptions=True,
                )
                tasks = t if not isinstance(t, Exception) else []
                notes = n if not isinstance(n, Exception) else []
            except Exception:
                pass
        errors = await detect_errors(deal, tasks, notes)
        return deal, errors

    # Enrich in batches of 5 to avoid hammering AmoCRM
    enriched: list[tuple[DealAnalysis, list[DealError]]] = []
    for i in range(0, len(deals), 5):
        batch = await asyncio.gather(*[_enrich(d) for d in deals[i:i + 5]])
        enriched.extend(batch)

    p1, p2, p3 = [], [], []
    for deal, errors in enriched:
        has_critical = any(e.severity == "critical" for e in errors)
        has_warning = any(e.severity == "warning" for e in errors)
        if has_critical or deal.heat == "hot":
            p1.append((deal, errors))
        elif has_warning or (deal.heat == "warm" and deal.days_inactive > 5):
            p2.append((deal, errors))
        elif deal.heat == "warm" or (deal.heat == "cold" and deal.lead_price >= _HIGH_VALUE):
            p3.append((deal, errors))

    lines = ["📋 <b>Твой план на сегодня</b>\n"]

    if p1:
        lines.append(f"🔴 <b>P1 — Срочно ({len(p1)})</b>")
        for deal, errors in p1[:_MAX_P1]:
            lines.append(_format_item(deal, errors, include_script=True))
        lines.append("")

    if p2:
        lines.append(f"🟡 <b>P2 — Сделать сегодня ({len(p2)})</b>")
        for deal, errors in p2[:_MAX_P2]:
            lines.append(_format_item(deal, errors))
        lines.append("")

    if p3:
        lines.append(f"⚪ <b>P3 — На контроле ({len(p3)})</b>")
        for deal, errors in p3[:_MAX_P3]:
            name = escape(deal.lead_name[:35])
            price = f" · {_fmt_price(deal.lead_price)}" if deal.lead_price else ""
            lines.append(
                f"• <a href=\"{escape(deal.amocrm_link, quote=True)}\">{name}</a>{price}"
                f" — {deal.days_inactive} дн."
            )
        lines.append("")

    total = len(p1) + len(p2) + len(p3)
    lines.append(
        f"<i>Сделок: {total} · 🔴 P1: {len(p1)} · 🟡 P2: {len(p2)} · ⚪ P3: {len(p3)}</i>"
    )

    return "\n".join(lines)


def _fmt_price(price: float) -> str:
    return f"{price:,.0f}₽".replace(",", " ") if price else "—"


def _format_item(
    deal: DealAnalysis,
    errors: list[DealError],
    include_script: bool = False,
) -> str:
    name = escape(deal.lead_name[:40])
    link = f'<a href="{escape(deal.amocrm_link, quote=True)}">{name}</a>'
    price = f" · {_fmt_price(deal.lead_price)}" if deal.lead_price else ""

    top_error = next(
        (e for e in errors if e.severity == "critical"),
        next((e for e in errors if e.severity == "warning"), None),
    )
    if top_error:
        issue = f" — <i>{escape(top_error.message)}</i>"
        action = f"\n  → {escape(top_error.recommendation)}"
    else:
        issue = f" — <i>{escape(deal.heat)}, {deal.days_inactive} дн.</i>"
        action = f"\n  → {escape(deal.next_step[:160])}" if deal.next_step else ""

    if include_script and deal.next_step and (not action or deal.next_step[:80] not in action):
        action += f"\n  Скрипт: {escape(deal.next_step[:220])}"
    return f"• {link}{price}{issue}{action}"
