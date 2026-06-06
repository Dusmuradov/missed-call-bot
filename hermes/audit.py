from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.amocrm.client import get_valid_client
from app.config import settings
from app.db import get_session
from app.models import HermesAuditCache

logger = logging.getLogger(__name__)
UTC = timezone.utc


@dataclass
class DealAnalysis:
    lead_id: int
    lead_name: str
    contact_name: str | None
    heat: str
    score: int
    reason: str
    next_step: str
    days_inactive: int
    lead_price: float
    status_name: str
    amocrm_link: str


def _days_inactive(lead: dict) -> int:
    for field in ("updated_at", "closest_task_at", "created_at"):
        ts = lead.get(field)
        if ts:
            try:
                return (datetime.now(UTC) - datetime.fromtimestamp(ts, UTC)).days
            except Exception:
                pass
    return 999


def _extract_contact(lead: dict) -> str | None:
    try:
        return lead["_embedded"]["contacts"][0]["name"]
    except (KeyError, IndexError, TypeError):
        return None


async def _analyse_one(
    lead: dict,
    client,
    with_suggestions: bool,
    skill_context: dict,
) -> DealAnalysis:
    lead_id = lead["id"]
    lead_name = lead.get("name") or f"Сделка #{lead_id}"
    days = _days_inactive(lead)
    price = float(lead.get("price") or 0)
    status_name = lead.get("status_name") or ""
    contact_name = _extract_contact(lead)
    subdomain = settings.amocrm_subdomain
    link = f"https://{subdomain}.amocrm.ru/leads/detail/{lead_id}"

    # Параллельно получаем notes и tasks
    notes, tasks = await asyncio.gather(
        client.get_lead_notes(lead_id),
        client.get_lead_tasks(lead_id),
        return_exceptions=True,
    )
    if isinstance(notes, Exception):
        notes = []
    if isinstance(tasks, Exception):
        tasks = []

    last_note = ""
    if notes:
        last_note = (notes[0].get("text") or notes[0].get("params", {}).get("text", ""))[:500]

    from hermes.skills.loader import call_skill

    heat_result = await call_skill("analyse_deal_heat", {
        "lead_name": lead_name,
        "lead_id": lead_id,
        "days_inactive": days,
        "lead_price": price,
        "status_name": status_name,
        "last_note": last_note,
        "open_tasks_count": len(tasks) if isinstance(tasks, list) else 0,
    }, skill_context)

    heat = heat_result.get("heat", "warm")
    score = int(heat_result.get("score", 5))
    reason = heat_result.get("reason", "")

    next_step = ""
    if with_suggestions:
        conv_result = {}
        if last_note:
            conv_result = await call_skill("analyse_conversation", {
                "lead_name": lead_name,
                "notes_text": last_note,
            }, skill_context)

        step_result = await call_skill("suggest_next_step", {
            "lead_name": lead_name,
            "heat": heat,
            "sentiment": conv_result.get("sentiment", "neutral"),
            "status_name": status_name,
            "client_objections": conv_result.get("client_objections", []),
        }, skill_context)

        action = step_result.get("action", "")
        script = step_result.get("script", "")
        next_step = f"{action}: {script}" if action and script else script or action

    return DealAnalysis(
        lead_id=lead_id,
        lead_name=lead_name,
        contact_name=contact_name,
        heat=heat,
        score=score,
        reason=reason,
        next_step=next_step,
        days_inactive=days,
        lead_price=price,
        status_name=status_name,
        amocrm_link=link,
    )


async def run_audit(
    amocrm_user_id: int,
    tg_user_id: int,
    force_refresh: bool = False,
    with_suggestions: bool = False,
) -> list[DealAnalysis]:
    # Проверяем кэш
    if not force_refresh:
        cached = await get_cached_audit(amocrm_user_id)
        if cached is not None:
            return cached

    from hermes.llm import get_llm_client
    from hermes.skills.loader import load_skills

    load_skills()  # убеждаемся что skills загружены
    skill_context = {"llm": get_llm_client()}

    client = await get_valid_client()
    if client is None:
        logger.error("AmoCRM client unavailable for audit (user=%d)", amocrm_user_id)
        return []

    leads = [l async for l in client.get_active_leads(amocrm_user_id)]
    logger.info("Audit: found %d active leads for amocrm_user=%d", len(leads), amocrm_user_id)

    if not leads:
        return []

    # Анализируем батчами по 5 (не заспамить AmoCRM API)
    results: list[DealAnalysis] = []
    batch_size = 5
    for i in range(0, len(leads), batch_size):
        batch = leads[i:i + batch_size]
        batch_results = await asyncio.gather(
            *[_analyse_one(lead, client, with_suggestions, skill_context) for lead in batch],
            return_exceptions=True,
        )
        for r in batch_results:
            if isinstance(r, Exception):
                logger.warning("Audit: deal analysis failed: %s", r)
            else:
                results.append(r)

    results.sort(key=lambda d: d.score, reverse=True)

    # Сохраняем в кэш
    try:
        expires = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=settings.hermes_audit_ttl_hours)
        async with get_session() as session:
            await session.execute(
                delete(HermesAuditCache).where(HermesAuditCache.amocrm_user_id == amocrm_user_id)
            )
            for deal in results:
                session.add(HermesAuditCache(
                    amocrm_user_id=amocrm_user_id,
                    lead_id=deal.lead_id,
                    heat=deal.heat,
                    recommendation=deal.next_step or deal.reason,
                    raw_analysis=json.dumps(asdict(deal), ensure_ascii=False),
                    expires_at=expires,
                ))
    except Exception as exc:
        logger.warning("Failed to save audit cache: %s", exc)

    return results


async def get_cached_audit(amocrm_user_id: int) -> list[DealAnalysis] | None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with get_session() as session:
        result = await session.execute(
            select(HermesAuditCache)
            .where(
                HermesAuditCache.amocrm_user_id == amocrm_user_id,
                HermesAuditCache.expires_at > now,
            )
            .order_by(HermesAuditCache.id.asc())
        )
        rows = list(result.scalars().all())

    if not rows:
        return None

    deals = []
    for row in rows:
        try:
            data = json.loads(row.raw_analysis)
            deals.append(DealAnalysis(**data))
        except Exception:
            pass
    return deals if deals else None
