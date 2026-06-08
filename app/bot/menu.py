"""Inline-клавиатуры и тексты меню для аналитического бота (с ролями)."""
from __future__ import annotations

from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.auth import ADMIN, MANAGER, SELLER
from app.periods import COMPARE_LABELS, PERIOD_LABELS


# ---------------------------------------------------------------------------
# Главное меню (зависит от роли)
# ---------------------------------------------------------------------------

def main_reply_keyboard(role: Optional[str] = None) -> ReplyKeyboardMarkup:
    """Постоянная клавиатура внизу чата. Набор кнопок зависит от роли."""
    if role == SELLER:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📞 Мои звонки"), KeyboardButton(text="🔴 Мои пропущенные")],
                [KeyboardButton(text="📋 Мои лиды AmoCRM")],
            ],
            resize_keyboard=True,
        )
    rows = [
        [KeyboardButton(text="📋 AmoCRM лиды"), KeyboardButton(text="📞 Звонки Utel")],
        [KeyboardButton(text="👤 Сотрудники CRM"), KeyboardButton(text="👥 По операторам")],
        [KeyboardButton(text="🔴 Пропущенные"), KeyboardButton(text="📈 Сравнения")],
    ]
    if role == ADMIN:
        rows.append([KeyboardButton(text="👥 Пользователи")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def main_menu_keyboard(role: Optional[str] = None) -> InlineKeyboardMarkup:
    """Главное меню отчётов. Набор кнопок зависит от роли пользователя."""

    if role == SELLER:
        return _seller_menu_keyboard()

    # admin / manager — полное меню
    rows = [
        [
            InlineKeyboardButton(text="📋 AmoCRM лиды", callback_data="menu:amocrm"),
            InlineKeyboardButton(text="📞 Звонки Utel", callback_data="menu:utel"),
        ],
        [
            InlineKeyboardButton(text="👤 Сотрудники CRM", callback_data="menu:amocrm_users"),
            InlineKeyboardButton(text="👥 По операторам",  callback_data="menu:operators"),
        ],
        [
            InlineKeyboardButton(text="🔴 Пропущенные",   callback_data="menu:missed"),
            InlineKeyboardButton(text="📈 Сравнения",     callback_data="menu:compare_type"),
        ],
    ]
    if role == ADMIN:
        rows.append([
            InlineKeyboardButton(text="👥 Пользователи", callback_data="menu:users"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _seller_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 Мои звонки",         callback_data="menu:my_utel")],
        [InlineKeyboardButton(text="🔴 Мои пропущенные",    callback_data="menu:my_missed")],
        [InlineKeyboardButton(text="📋 Мои лиды AmoCRM",    callback_data="menu:my_amocrm")],
    ])


# ---------------------------------------------------------------------------
# Меню выбора периода
# ---------------------------------------------------------------------------

def period_keyboard(prefix: str, back: str = "menu:main") -> InlineKeyboardMarkup:
    """Клавиатура выбора периода для указанного типа отчёта (prefix)."""
    rows = []
    items = list(PERIOD_LABELS.items())
    for i in range(0, len(items), 2):
        row = []
        for key, label in items[i:i+2]:
            row.append(InlineKeyboardButton(text=label, callback_data=f"{prefix}:{key}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Меню сравнений
# ---------------------------------------------------------------------------

def compare_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Лиды AmoCRM", callback_data="cmp_type:amocrm")],
        [InlineKeyboardButton(text="📞 Звонки Utel",  callback_data="cmp_type:utel")],
        [InlineKeyboardButton(text="« Назад", callback_data="menu:main")],
    ])


def compare_period_keyboard(data_type: str) -> InlineKeyboardMarkup:
    rows = []
    for key, label in COMPARE_LABELS.items():
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"compare:{data_type}:{key}")
        ])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="menu:compare_type")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Кнопка «Перезвонил»
# ---------------------------------------------------------------------------

def callback_done_keyboard(tracking_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Перезвонил", callback_data=f"callback_done:{tracking_id}")],
    ])


# ---------------------------------------------------------------------------
# Клавиатуры управления пользователями (только admin)
# ---------------------------------------------------------------------------

def role_select_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора роли при первичной регистрации."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Сотрудник", callback_data="reg_role:seller")],
        [InlineKeyboardButton(text="👔 Начальник",  callback_data="reg_role:manager")],
    ])


def approval_keyboard(uid: int) -> InlineKeyboardMarkup:
    """Кнопки одобрения заявки нового пользователя."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Продавец",   callback_data=f"approve:{uid}:seller"),
            InlineKeyboardButton(text="✅ Начальник",  callback_data=f"approve:{uid}:manager"),
        ],
        [InlineKeyboardButton(text="⛔ Отклонить", callback_data=f"reject:{uid}")],
    ])


def seller_ext_picker(uid: int) -> InlineKeyboardMarkup:
    """Пикер Utel-номера при одобрении продавца."""
    from app.operators import list_operators
    ops = list_operators()
    rows = []
    for ext, name in sorted(ops.items()):
        rows.append([
            InlineKeyboardButton(text=f"📞 {ext} — {name}", callback_data=f"setext:{uid}:{ext}")
        ])
    rows.append([InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"setext:{uid}:-")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def seller_amo_picker(uid: int, users: list[dict]) -> InlineKeyboardMarkup:
    """Пикер пользователя AmoCRM при одобрении продавца."""
    rows = []
    for u in users:
        aid = u.get("id", 0)
        name = u.get("name") or u.get("email") or f"ID {aid}"
        rows.append([
            InlineKeyboardButton(text=f"👤 {name}", callback_data=f"setamo:{uid}:{aid}")
        ])
    rows.append([InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"setamo:{uid}:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def users_list_keyboard(users) -> InlineKeyboardMarkup:
    """Список пользователей с кнопкой удаления."""
    from app.auth import ROLE_LABELS
    rows = []
    for u in users:
        label = ROLE_LABELS.get(u.role, u.role)
        name = u.full_name or u.username or str(u.tg_user_id)
        rows.append([
            InlineKeyboardButton(
                text=f"{label}: {name}",
                callback_data=f"users:info:{u.tg_user_id}",
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"users:remove:{u.tg_user_id}"),
        ])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_confirm_remove_keyboard(uid: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения удаления пользователя."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"users:confirm_remove:{uid}"),
            InlineKeyboardButton(text="← Назад",        callback_data="menu:users"),
        ],
    ])


MAIN_MENU_TEXT = "📊 <b>Аналитика</b>\n\nВыберите раздел:"
