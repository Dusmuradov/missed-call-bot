"""CRUD-операции с БД: запись звонков, статусы перезвонов, AmoCRM-токен."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AmocrmToken, BillzSnapshot, BillzToken, BotUser, Call, CrmToken, MissedTracking

logger = logging.getLogger(__name__)

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Call
# ---------------------------------------------------------------------------

async def save_call(
    session: AsyncSession,
    *,
    call_id: Optional[str],
    direction: Optional[str],
    external_number: Optional[str],
    operator_ext: Optional[str],
    operator_name: Optional[str],
    call_time_utc: Optional[datetime],
    status_name: Optional[str],
    status_number: Optional[int],
    wait_seconds: Optional[int],
    answered: bool,
    raw: dict,
) -> Optional[Call]:
    """
    Сохраняет запись о звонке. Возвращает объект Call или None если call_id — дубль.
    Заменяет in-memory _SEEN_CALL_IDS дедупликацию из старого main.py.
    """
    call = Call(
        call_id=call_id,
        direction=direction,
        external_number=external_number,
        operator_ext=operator_ext,
        operator_name=operator_name,
        call_time_utc=call_time_utc,
        status_name=status_name,
        status_number=status_number,
        wait_seconds=wait_seconds,
        answered=answered,
        raw_json=json.dumps(raw, ensure_ascii=False)[:4096],
        received_at=_now_utc(),
    )
    session.add(call)
    try:
        await session.flush()  # Сразу проверяем unique constraint на call_id
        return call
    except IntegrityError:
        await session.rollback()
        logger.debug("Duplicate call_id=%s — skipped.", call_id)
        return None


async def get_call_by_id(session: AsyncSession, call_id: str) -> Optional[Call]:
    result = await session.execute(select(Call).where(Call.call_id == call_id))
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# MissedTracking
# ---------------------------------------------------------------------------

async def create_missed_tracking(
    session: AsyncSession,
    *,
    call: Call,
    external_number: Optional[str],
    operator_ext: Optional[str],
    missed_at_utc: datetime,
) -> MissedTracking:
    """Создаёт запись отслеживания перезвона для входящего пропущенного."""
    mt = MissedTracking(
        call_id_fk=call.id,
        external_number=external_number,
        operator_ext=operator_ext,
        missed_at_utc=missed_at_utc,
    )
    session.add(mt)
    await session.flush()
    return mt


async def find_open_missed(
    session: AsyncSession, external_number: str
) -> list[MissedTracking]:
    """Находит незакрытые (не перезвоненные) треки по номеру клиента."""
    result = await session.execute(
        select(MissedTracking)
        .where(
            MissedTracking.external_number == external_number,
            MissedTracking.called_back.is_(False),
        )
        .order_by(MissedTracking.missed_at_utc.asc())
    )
    return list(result.scalars().all())


async def mark_called_back(
    session: AsyncSession,
    tracking: MissedTracking,
    *,
    called_back_by: Optional[str] = None,
    manual: bool = False,
) -> None:
    """Помечает перезвон выполненным (автоматически или вручную)."""
    tracking.called_back = True
    tracking.called_back_at = _now_utc()
    tracking.called_back_by = called_back_by
    tracking.manual = manual
    session.add(tracking)
    await session.flush()


async def mark_escalated(
    session: AsyncSession,
    tracking: MissedTracking,
    *,
    tg_message_id: Optional[int] = None,
) -> None:
    """Помечает эскалацию отправленной."""
    tracking.escalated = True
    tracking.escalated_at = _now_utc()
    tracking.tg_message_id = tg_message_id
    session.add(tracking)
    await session.flush()


async def get_tracking_by_id(session: AsyncSession, tracking_id: str) -> Optional[MissedTracking]:
    result = await session.execute(
        select(MissedTracking).where(MissedTracking.id == tracking_id)
    )
    return result.scalar_one_or_none()


async def get_unescalated_overdue(
    session: AsyncSession, before_utc: datetime
) -> list[MissedTracking]:
    """
    Возвращает пропущенные звонки, по которым не перезвонили и не отправляли эскалацию,
    и которые старше before_utc (т.е. прошло более N минут).
    """
    result = await session.execute(
        select(MissedTracking)
        .where(
            MissedTracking.called_back.is_(False),
            MissedTracking.escalated.is_(False),
            MissedTracking.missed_at_utc <= before_utc,
        )
        .order_by(MissedTracking.missed_at_utc.asc())
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# BotUser (RBAC)
# ---------------------------------------------------------------------------

async def get_bot_user(session: AsyncSession, tg_user_id: int) -> Optional[BotUser]:
    result = await session.execute(
        select(BotUser).where(BotUser.tg_user_id == tg_user_id)
    )
    return result.scalar_one_or_none()


async def list_bot_users(
    session: AsyncSession, role: Optional[str] = None
) -> list[BotUser]:
    q = select(BotUser)
    if role is not None:
        q = q.where(BotUser.role == role)
    q = q.order_by(BotUser.created_at.asc())
    result = await session.execute(q)
    return list(result.scalars().all())


async def list_pending_users(session: AsyncSession) -> list[BotUser]:
    return await list_bot_users(session, role="pending")


async def create_pending_user(
    session: AsyncSession,
    tg_user_id: int,
    username: Optional[str],
    full_name: Optional[str],
    utel_ext: Optional[str] = None,
    amocrm_user_id: Optional[int] = None,
) -> BotUser:
    """Создаёт запись со статусом pending. Idempotent: если уже есть — возвращает существующую."""
    existing = await get_bot_user(session, tg_user_id)
    if existing is not None:
        return existing
    user = BotUser(
        tg_user_id=tg_user_id,
        username=username,
        full_name=full_name,
        role="pending",
        utel_ext=utel_ext,
        amocrm_user_id=amocrm_user_id,
    )
    session.add(user)
    await session.flush()
    return user


async def set_user_role(
    session: AsyncSession,
    tg_user_id: int,
    role: str,
    approved_by: Optional[int] = None,
) -> Optional[BotUser]:
    user = await get_bot_user(session, tg_user_id)
    if user is None:
        return None
    user.role = role
    if approved_by is not None:
        user.approved_by = approved_by
    session.add(user)
    await session.flush()
    return user


async def set_seller_mapping(
    session: AsyncSession,
    tg_user_id: int,
    utel_ext: Optional[str] = None,
    amocrm_user_id: Optional[int] = None,
) -> Optional[BotUser]:
    user = await get_bot_user(session, tg_user_id)
    if user is None:
        return None
    if utel_ext is not None:
        user.utel_ext = None if utel_ext == "-" else utel_ext
    if amocrm_user_id is not None:
        user.amocrm_user_id = None if amocrm_user_id == 0 else amocrm_user_id
    session.add(user)
    await session.flush()
    return user


async def delete_bot_user(session: AsyncSession, tg_user_id: int) -> bool:
    user = await get_bot_user(session, tg_user_id)
    if user is None:
        return False
    await session.delete(user)
    await session.flush()
    return True


# ---------------------------------------------------------------------------
# AmocrmToken
# ---------------------------------------------------------------------------

async def get_amocrm_token(session: AsyncSession) -> Optional[AmocrmToken]:
    result = await session.execute(select(AmocrmToken).where(AmocrmToken.id == 1))
    return result.scalar_one_or_none()


async def upsert_amocrm_token(
    session: AsyncSession,
    *,
    subdomain: str,
    access_token: str,
    refresh_token: str,
    expires_at: Optional[datetime],
) -> AmocrmToken:
    token = await get_amocrm_token(session)
    if token is None:
        token = AmocrmToken(id=1, subdomain=subdomain)
        session.add(token)
    token.subdomain = subdomain
    token.access_token = access_token
    token.refresh_token = refresh_token
    token.expires_at = expires_at
    await session.flush()
    return token


# ---------------------------------------------------------------------------
# BillzToken
# ---------------------------------------------------------------------------

async def get_billz_token(session: AsyncSession) -> Optional[BillzToken]:
    result = await session.execute(select(BillzToken).where(BillzToken.id == 1))
    return result.scalar_one_or_none()


async def upsert_billz_token(
    session: AsyncSession,
    *,
    access_token: str,
    refresh_token: str,
    expires_at: Optional[datetime],
) -> BillzToken:
    row = await get_billz_token(session)
    if row is None:
        row = BillzToken(id=1)
        session.add(row)
    row.access_token = access_token
    row.refresh_token = refresh_token
    row.expires_at = expires_at
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# CrmToken
# ---------------------------------------------------------------------------

async def get_crm_token(session: AsyncSession) -> Optional[CrmToken]:
    result = await session.execute(select(CrmToken).where(CrmToken.id == 1))
    return result.scalar_one_or_none()


async def upsert_crm_token(
    session: AsyncSession,
    *,
    access_token: str,
    m2m_token: Optional[str] = None,
    m2m_expires_at: Optional[datetime] = None,
) -> CrmToken:
    row = await get_crm_token(session)
    if row is None:
        row = CrmToken(id=1)
        session.add(row)
    row.access_token = access_token
    if m2m_token is not None:
        row.m2m_token = m2m_token
    if m2m_expires_at is not None:
        row.m2m_expires_at = m2m_expires_at
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# BillzSnapshot
# ---------------------------------------------------------------------------

async def get_billz_snapshot(session: AsyncSession, snapshot_date: str) -> Optional[BillzSnapshot]:
    """Возвращает снимок KPI за указанную дату (yyyy-MM-dd) или None."""
    result = await session.execute(
        select(BillzSnapshot).where(BillzSnapshot.snapshot_date == snapshot_date)
    )
    return result.scalar_one_or_none()


async def save_billz_snapshot(
    session: AsyncSession,
    *,
    snapshot_date: str,
    revenue: float,
    orders: int,
    aov: float,
    items_sold: float,
) -> BillzSnapshot:
    """Upsert: создаёт или обновляет снимок за дату."""
    row = await get_billz_snapshot(session, snapshot_date)
    if row is None:
        row = BillzSnapshot(snapshot_date=snapshot_date)
        session.add(row)
    row.revenue = revenue
    row.orders = orders
    row.aov = aov
    row.items_sold = items_sold
    await session.flush()
    return row
