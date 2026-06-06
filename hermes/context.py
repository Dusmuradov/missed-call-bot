from __future__ import annotations

from sqlalchemy import delete, select

from app.db import get_session
from app.models import HermesConversation


async def get_history(tg_user_id: int, limit: int = 20) -> list[dict]:
    """Возвращает последние N сообщений в хронологическом порядке (от старых к новым)."""
    async with get_session() as session:
        result = await session.execute(
            select(HermesConversation)
            .where(HermesConversation.tg_user_id == tg_user_id)
            .order_by(HermesConversation.id.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())
    rows.reverse()
    return [{"role": r.role, "content": r.content} for r in rows]


async def save_message(tg_user_id: int, role: str, content: str) -> None:
    """Сохраняет одно сообщение в историю диалога."""
    async with get_session() as session:
        session.add(HermesConversation(
            tg_user_id=tg_user_id,
            role=role,
            content=content,
        ))


async def clear_history(tg_user_id: int) -> int:
    """Удаляет всю историю диалога пользователя. Возвращает кол-во удалённых записей."""
    async with get_session() as session:
        result = await session.execute(
            delete(HermesConversation)
            .where(HermesConversation.tg_user_id == tg_user_id)
        )
        return result.rowcount


async def trim_history(tg_user_id: int, keep: int = 50) -> None:
    """Оставляет только последние keep сообщений, удаляя старые."""
    async with get_session() as session:
        # Находим id порогового сообщения
        result = await session.execute(
            select(HermesConversation.id)
            .where(HermesConversation.tg_user_id == tg_user_id)
            .order_by(HermesConversation.id.desc())
            .offset(keep)
            .limit(1)
        )
        threshold_id = result.scalar_one_or_none()
        if threshold_id is not None:
            await session.execute(
                delete(HermesConversation)
                .where(
                    HermesConversation.tg_user_id == tg_user_id,
                    HermesConversation.id <= threshold_id,
                )
            )
