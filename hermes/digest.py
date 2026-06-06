from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings
from hermes.audit import DealAnalysis

_HEAT_EMOJI = {"hot": "", "warm": "", "cold": ""}
_MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def _fmt_price(price: float) -> str:
    return f"{price:,.0f}₽".replace(",", " ") if price else "—"


def _fmt_deal_line(i: int, d: DealAnalysis) -> str:
    step = d.next_step or d.reason
    return (
        f"{i}. <b>{d.lead_name}</b> — {_fmt_price(d.lead_price)} · {d.days_inactive} дн.\n"
        f"   → {step[:120]}"
    )


def format_digest(manager_name: str, deals: list[DealAnalysis], top_n: int = 5) -> str:
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    date_str = f"{now.day} {_MONTHS_RU[now.month]} {now.year}"
    time_str = now.strftime("%H:%M")

    hot = [d for d in deals if d.heat == "hot"]
    warm = [d for d in deals if d.heat == "warm"]
    cold = [d for d in deals if d.heat == "cold"]

    lines = [f"☀️ Доброе утро, {manager_name}!", f"", f"Твои сделки на сегодня — {date_str}", ""]

    for emoji, label, group in [
        ("", "Горячие", hot),
        ("", "Тёплые", warm),
        ("", "Холодные", cold),
    ]:
        if not group:
            continue
        shown = group[:top_n]
        lines.append(f"{emoji} <b>{label} ({len(group)})</b>:")
        for i, d in enumerate(shown, 1):
            lines.append(_fmt_deal_line(i, d))
        if len(group) > top_n:
            lines.append(f"   …ещё {len(group) - top_n}")
        lines.append("")

    lines.append(f"Всего активных сделок: {len(deals)}")
    lines.append(f"Обновлено: {time_str}")
    return "\n".join(lines)


def format_deal_card(deal: DealAnalysis) -> str:
    emoji = _HEAT_EMOJI.get(deal.heat, "⚪")
    lines = [
        f" <b>{deal.lead_name}</b>",
        f" Сумма: {_fmt_price(deal.lead_price)}",
        f" Статус: {deal.status_name or '—'}",
        f" Приоритет: {emoji} {deal.heat}",
        f"⏰ Без активности: {deal.days_inactive} дн.",
    ]
    if deal.contact_name:
        lines.append(f" Контакт: {deal.contact_name}")
    lines.append("")
    lines.append(f" Анализ: {deal.reason}")
    if deal.next_step:
        lines.append(f"➡️ Следующий шаг: {deal.next_step}")
    lines.append("")
    lines.append(f' <a href="{deal.amocrm_link}">Открыть в AmoCRM</a>')
    return "\n".join(lines)


def format_top_hot(deals: list[DealAnalysis], top_n: int = 7) -> str:
    priority = [d for d in deals if d.heat in ("hot", "warm")]
    if not priority:
        priority = deals

    lines = [" <b>Топ сделок на сегодня</b>", ""]
    for i, d in enumerate(priority[:top_n], 1):
        emoji = _HEAT_EMOJI.get(d.heat, "⚪")
        step = (d.next_step or d.reason)[:100]
        lines.append(
            f'{i}. <a href="{d.amocrm_link}">{d.lead_name}</a> — {_fmt_price(d.lead_price)}\n'
            f'   {emoji} {d.heat.capitalize()} · {d.days_inactive} дн. · {step}'
        )
    return "\n".join(lines)
