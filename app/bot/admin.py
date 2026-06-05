"""
Административные хендлеры: управление пользователями, одобрение заявок.
Доступно только роли admin.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.auth import ADMIN, ROLE_LABELS, has_access, resolve_role
from app.bot.menu import (
    approval_keyboard,
    seller_amo_picker,
    seller_ext_picker,
    users_list_keyboard,
)

logger = logging.getLogger(__name__)
router = Router()


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

async def _is_admin(uid: int) -> bool:
    from app.db import get_session
    async with get_session() as session:
        role = await resolve_role(session, uid)
    return has_access(role, ADMIN)


async def _deny_admin(cq: CallbackQuery) -> None:
    await cq.answer("⛔ Только для администратора.", show_alert=True)


# ---------------------------------------------------------------------------
# Список пользователей
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "menu:users")
async def cb_menu_users(cq: CallbackQuery) -> None:
    if not await _is_admin(cq.from_user.id):
        await _deny_admin(cq)
        return

    from app.db import get_session
    from app.repository import list_bot_users
    async with get_session() as session:
        users = await list_bot_users(session)

    if not users:
        await cq.message.edit_text(
            "👥 <b>Пользователи</b>\n\nСписок пуст.",
            reply_markup=users_list_keyboard([]),
        )
    else:
        await cq.message.edit_text(
            f"👥 <b>Пользователи</b> ({len(users)} чел.)\n\n"
            "Нажмите 🗑 для удаления:",
            reply_markup=users_list_keyboard(users),
        )
    await cq.answer()


# ---------------------------------------------------------------------------
# Удаление пользователя
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("users:remove:"))
async def cb_users_remove(cq: CallbackQuery) -> None:
    if not await _is_admin(cq.from_user.id):
        await _deny_admin(cq)
        return

    uid = int(cq.data.split(":")[-1])
    from app.db import get_session
    from app.repository import delete_bot_user, list_bot_users
    async with get_session() as session:
        deleted = await delete_bot_user(session, uid)
        users = await list_bot_users(session)

    if deleted:
        await cq.answer(f"✅ Пользователь {uid} удалён.")
    else:
        await cq.answer("Пользователь не найден.")

    await cq.message.edit_text(
        f"👥 <b>Пользователи</b> ({len(users)} чел.)\n\nНажмите 🗑 для удаления:",
        reply_markup=users_list_keyboard(users),
    )


# ---------------------------------------------------------------------------
# Одобрение заявки: approve:<uid>:manager / approve:<uid>:seller
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("approve:"))
async def cb_approve(cq: CallbackQuery) -> None:
    if not await _is_admin(cq.from_user.id):
        await _deny_admin(cq)
        return

    parts = cq.data.split(":")  # approve:<uid>:<role>
    uid = int(parts[1])
    role = parts[2]

    from app.db import get_session
    from app.repository import set_user_role
    async with get_session() as session:
        user = await set_user_role(session, uid, role, approved_by=cq.from_user.id)

    if user is None:
        await cq.answer("Пользователь не найден в БД.", show_alert=True)
        return

    role_label = ROLE_LABELS.get(role, role)
    name = user.full_name or user.username or str(uid)
    await cq.answer(f"✅ {name} → {role_label}")

    if role == "seller":
        # Если при регистрации уже заполнены utel_ext и amocrm_user_id — пропускаем пикеры
        if user.utel_ext and user.amocrm_user_id:
            ext_info = f"📞 Utel: {user.utel_ext}\n📋 AmoCRM ID: {user.amocrm_user_id}"
            await _finish_seller_approval(cq, uid, name, ext_info)
        elif user.utel_ext:
            # Utel есть, нужен AmoCRM
            try:
                from app.amocrm.client import get_valid_client
                client = await get_valid_client()
                amo_users = await client.get_users() if client else []
            except Exception:
                amo_users = []
            ext_info = f"📞 Utel: {user.utel_ext}"
            if amo_users:
                await cq.message.edit_text(
                    f"👤 <b>{name}</b>\n{ext_info}\n\nВыберите аккаунт AmoCRM:",
                    reply_markup=seller_amo_picker(uid, amo_users),
                )
            else:
                await _finish_seller_approval(cq, uid, name, ext_info)
        else:
            # Utel не заполнен — показываем пикер
            await cq.message.edit_text(
                f"👤 <b>{name}</b> одобрен как продавец.\n\n"
                "Выберите Utel-номер оператора:",
                reply_markup=seller_ext_picker(uid),
            )
    else:
        # manager — готово, уведомить пользователя
        await cq.message.edit_text(
            f"✅ <b>{name}</b> одобрен как {role_label}."
        )
        from app.telegram import send_to_user
        from app.bot.menu import main_menu_keyboard, MAIN_MENU_TEXT
        await send_to_user(
            uid,
            f"✅ Ваш доступ одобрен!\nРоль: {role_label}\n\n" + MAIN_MENU_TEXT,
            reply_markup=main_menu_keyboard(role),
        )


# ---------------------------------------------------------------------------
# Выбор Utel-ext для продавца
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("setext:"))
async def cb_setext(cq: CallbackQuery) -> None:
    if not await _is_admin(cq.from_user.id):
        await _deny_admin(cq)
        return

    parts = cq.data.split(":")  # setext:<uid>:<ext>
    uid = int(parts[1])
    ext = parts[2]  # "-" означает «пропустить»

    from app.db import get_session
    from app.repository import get_bot_user, set_seller_mapping
    async with get_session() as session:
        await set_seller_mapping(session, uid, utel_ext=ext)
        user = await get_bot_user(session, uid)

    name = (user.full_name or user.username or str(uid)) if user else str(uid)
    ext_info = f"📞 Utel: {ext}" if ext != "-" else "📞 Utel: не привязан"
    await cq.answer(f"Сохранено: {ext_info}")

    # Следующий шаг: AmoCRM пользователь
    try:
        from app.amocrm.client import get_valid_client
        from app.db import get_session as gs
        async with gs() as session:
            client = await get_valid_client()
        amo_users = await client.get_users() if client else []
    except Exception:
        amo_users = []

    if amo_users:
        await cq.message.edit_text(
            f"👤 <b>{name}</b>\n{ext_info}\n\n"
            "Выберите аккаунт AmoCRM:",
            reply_markup=seller_amo_picker(uid, amo_users),
        )
    else:
        # AmoCRM недоступен — завершаем
        await _finish_seller_approval(cq, uid, name, ext_info)


# ---------------------------------------------------------------------------
# Выбор AmoCRM-пользователя для продавца
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("setamo:"))
async def cb_setamo(cq: CallbackQuery) -> None:
    if not await _is_admin(cq.from_user.id):
        await _deny_admin(cq)
        return

    parts = cq.data.split(":")   # setamo:<uid>:<amocrm_id>
    uid = int(parts[1])
    amo_id = int(parts[2])  # 0 = пропустить

    from app.db import get_session
    from app.repository import get_bot_user, set_seller_mapping
    async with get_session() as session:
        await set_seller_mapping(session, uid, amocrm_user_id=amo_id)
        user = await get_bot_user(session, uid)

    name = (user.full_name or user.username or str(uid)) if user else str(uid)
    ext_info = f"📞 Utel: {user.utel_ext or '—'}" if user else ""
    amo_info = f"📋 AmoCRM ID: {amo_id}" if amo_id else "📋 AmoCRM: не привязан"

    await cq.answer("Сохранено")
    await _finish_seller_approval(cq, uid, name, f"{ext_info}\n{amo_info}")


async def _finish_seller_approval(
    cq: CallbackQuery, uid: int, name: str, mapping_info: str
) -> None:
    """Завершает одобрение продавца — уведомляет его."""
    await cq.message.edit_text(
        f"✅ <b>{name}</b> одобрен как продавец.\n{mapping_info}"
    )
    from app.bot.menu import MAIN_MENU_TEXT, main_menu_keyboard
    from app.telegram import send_to_user
    await send_to_user(
        uid,
        "✅ Ваш доступ одобрен!\nРоль: 🛒 Продавец\n\n" + MAIN_MENU_TEXT,
        reply_markup=main_menu_keyboard("seller"),
    )


# ---------------------------------------------------------------------------
# Отклонение заявки
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("reject:"))
async def cb_reject(cq: CallbackQuery) -> None:
    if not await _is_admin(cq.from_user.id):
        await _deny_admin(cq)
        return

    uid = int(cq.data.split(":")[-1])
    from app.db import get_session
    from app.repository import get_bot_user, set_user_role
    async with get_session() as session:
        user = await set_user_role(session, uid, "rejected", approved_by=cq.from_user.id)
        if user is None:
            await cq.answer("Пользователь не найден.", show_alert=True)
            return
        name = user.full_name or user.username or str(uid)

    await cq.answer(f"⛔ {name} отклонён.")
    await cq.message.edit_text(f"⛔ <b>{name}</b> отклонён.")
    from app.telegram import send_to_user
    await send_to_user(uid, "⛔ Ваша заявка на доступ отклонена.")
