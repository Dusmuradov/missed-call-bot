"""
Ролевая модель доступа (RBAC) для Telegram-бота.

Роли: admin > manager > seller  (pending / rejected — нет доступа).
Главный администратор фиксируется в .env (ADMIN_USER_ID) и всегда получает
роль admin независимо от БД — это bootstrap, чтобы не потерять доступ.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

# ---------------------------------------------------------------------------
# Константы ролей
# ---------------------------------------------------------------------------

ADMIN    = "admin"
MANAGER  = "manager"
SELLER   = "seller"
PENDING  = "pending"
REJECTED = "rejected"

# Числовой уровень — чем больше, тем больше прав
LEVELS: dict[str, int] = {
    SELLER:  1,
    MANAGER: 2,
    ADMIN:   3,
}

ROLE_LABELS: dict[str, str] = {
    ADMIN:    "👑 Администратор",
    MANAGER:  "👔 Начальник магазина",
    SELLER:   "🛒 Продавец",
    PENDING:  "⏳ Ожидает одобрения",
    REJECTED: "⛔ Отклонён",
}


# ---------------------------------------------------------------------------
# Функции
# ---------------------------------------------------------------------------

async def resolve_role(session: AsyncSession, uid: int) -> Optional[str]:
    """
    Возвращает роль пользователя.

    - Если uid == settings.admin_user_id (и он не 0) → ADMIN (bootstrap).
    - Иначе берём BotUser.role из БД.
    - Если записи нет → None (неизвестный пользователь).
    """
    if settings.admin_user_id and uid == settings.admin_user_id:
        return ADMIN

    from app.repository import get_bot_user
    user = await get_bot_user(session, uid)
    if user is None:
        return None
    return user.role


def has_access(role: Optional[str], min_role: str) -> bool:
    """True если уровень role >= уровень min_role."""
    if role is None:
        return False
    return LEVELS.get(role, 0) >= LEVELS.get(min_role, 0)
