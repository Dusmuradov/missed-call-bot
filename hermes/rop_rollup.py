"""
Stage 3: РОП team roll-up — aggregated view for manager/admin.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _SellerSummary:
    name: str
    total: int
    hot: int
    warm: int
    cold: int
    critical_errors: int
    warning_errors: int
    top_risks: list[str] = field(default_factory=list)


async def build_rop_rollup() -> str:
    """Return Telegram HTML team-wide summary for the manager/РОП."""
    from app.db import get_session
    from app.repository import list_bot_users
    from hermes.audit import run_audit
    from hermes.rop_errors import detect_errors

    async with get_session() as session:
        all_users = await list_bot_users(session)

    targets = [u for u in all_users if u.amocrm_user_id and u.role in ("seller", "manager")]
    if not targets:
        return "📊 <b>Сводка команды</b>\n\nНет сотрудников с привязкой к AmoCRM."

    async def _analyse(user):
        try:
            deals = await run_audit(user.amocrm_user_id, user.tg_user_id, with_suggestions=False)
        except Exception as exc:
            logger.error("rop_rollup: audit failed for user=%d: %s", user.tg_user_id, exc)
            return None

        name = user.full_name or user.username or f"ID{user.tg_user_id}"
        if not deals:
            return _SellerSummary(name=name, total=0, hot=0, warm=0, cold=0,
                                  critical_errors=0, warning_errors=0)

        crit = warn = 0
        risks: list[str] = []
        for deal in deals:
            # Skip tasks/notes API calls in team report — use heat/days/price/status only
            errors = await detect_errors(deal, tasks=None, notes=None)
            crit += sum(1 for e in errors if e.severity == "critical")
            warn += sum(1 for e in errors if e.severity == "warning")
            if errors and deal.heat in ("hot", "warm"):
                risks.append(f"{deal.lead_name[:25]} — {errors[0].message}")

        return _SellerSummary(
            name=name,
            total=len(deals),
            hot=sum(1 for d in deals if d.heat == "hot"),
            warm=sum(1 for d in deals if d.heat == "warm"),
            cold=sum(1 for d in deals if d.heat == "cold"),
            critical_errors=crit,
            warning_errors=warn,
            top_risks=risks[:2],
        )

    results = await asyncio.gather(*[_analyse(u) for u in targets], return_exceptions=True)
    summaries: list[_SellerSummary] = [
        r for r in results if isinstance(r, _SellerSummary)
    ]

    if not summaries:
        return "📊 <b>Сводка команды</b>\n\nДанных нет."

    summaries.sort(key=lambda s: s.critical_errors, reverse=True)

    total_deals = sum(s.total for s in summaries)
    total_hot = sum(s.hot for s in summaries)
    total_crit = sum(s.critical_errors for s in summaries)

    lines = [
        "📊 <b>Сводка команды — РОП</b>\n",
        f"Сотрудников: {len(summaries)} · Сделок: {total_deals} · "
        f"Горячих: {total_hot} · Критичных ошибок: {total_crit}\n",
    ]

    for s in summaries:
        icon = "🔴" if s.critical_errors else ("🟡" if s.warning_errors else "✅")
        lines.append(
            f"{icon} <b>{s.name}</b>: {s.total} сд "
            f"({s.hot}🔴 {s.warm}🟡 {s.cold}⚪)"
            f" · ошибок: <b>{s.critical_errors}</b> крит / {s.warning_errors} пред"
        )
        for risk in s.top_risks:
            lines.append(f"  ⚠ {risk}")

    worst = summaries[0] if summaries and summaries[0].critical_errors > 0 else None
    if worst:
        lines.append(
            f"\n💬 <b>Фокус РОПа:</b> {worst.name} — {worst.critical_errors} критичных ошибок. "
            "Используй /plan для детального разбора."
        )

    return "\n".join(lines)
