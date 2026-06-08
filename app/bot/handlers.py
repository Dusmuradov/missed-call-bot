"""
aiogram хендлеры: команды, inline-кнопки, продавец, «Перезвонил».
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.auth import ADMIN, MANAGER, SELLER, has_access, resolve_role
from app.bot.menu import (
    MAIN_MENU_TEXT,
    callback_done_keyboard,
    compare_period_keyboard,
    compare_type_keyboard,
    main_menu_keyboard,
    main_reply_keyboard,
    period_keyboard,
    role_select_keyboard,
    users_list_keyboard,
)
from app.periods import COMPARE_FUNCS, COMPARE_LABELS, PERIOD_FUNCS, PERIOD_LABELS

logger = logging.getLogger(__name__)
router = Router()


class Registration(StatesGroup):
    waiting_utel_code = State()


# ---------------------------------------------------------------------------
# Вспомогательные функции доступа
# ---------------------------------------------------------------------------

async def _get_role(uid: int) -> str | None:
    from app.db import get_session
    async with get_session() as session:
        return await resolve_role(session, uid)


async def _check(cq: CallbackQuery, min_role: str) -> str | None:
    """Возвращает роль если доступ есть, иначе отклоняет и возвращает None."""
    role = await _get_role(cq.from_user.id)
    if not has_access(role, min_role):
        await cq.answer("⛔ Недостаточно прав.", show_alert=True)
        return None
    return role


# ---------------------------------------------------------------------------
# /myid — узнать свой Telegram user_id
# ---------------------------------------------------------------------------

@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    await message.answer(f"🆔 Ваш Telegram ID: <code>{message.from_user.id}</code>")


# ---------------------------------------------------------------------------
# /start и /report
# ---------------------------------------------------------------------------

@router.message(Command("start", "report"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    role = await _get_role(uid)

    if role is None:
        await state.clear()
        await message.answer(
            "👋 Добро пожаловать!\n\n"
            "Выберите вашу роль:",
            reply_markup=role_select_keyboard(),
        )
        return

    if role == "pending":
        await message.answer("⏳ Ваша заявка ещё на рассмотрении. Ожидайте.")
        return

    if role == "rejected":
        await message.answer("⛔ Доступ закрыт. Обратитесь к администратору.")
        return

    await message.answer(MAIN_MENU_TEXT, reply_markup=main_reply_keyboard(role))

# ---------------------------------------------------------------------------
# Шаг регистрации: ввод кода оператора Utel
# ---------------------------------------------------------------------------

@router.message(Registration.waiting_utel_code)
async def reg_utel_code(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    utel_ext = (message.text or "").strip()

    if not utel_ext:
        await message.answer("Пожалуйста, введите код оператора (например: <code>101</code>):")
        return

    from app.config import settings
    from app.db import get_session
    from app.repository import create_pending_user
    from app.telegram import send_to_user
    from app.bot.menu import approval_keyboard

    amocrm_user_id = settings.utel_to_amocrm().get(utel_ext)

    async with get_session() as session:
        await create_pending_user(
            session,
            tg_user_id=uid,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            utel_ext=utel_ext,
            amocrm_user_id=amocrm_user_id,
        )

    await state.clear()

    amo_line = f"AmoCRM привязан автоматически ✅" if amocrm_user_id else "AmoCRM: не найден в маппинге"
    await message.answer(
        f"✅ Код оператора <code>{utel_ext}</code> сохранён.\n"
        f"{amo_line}\n\n"
        "⏳ <b>Заявка отправлена</b>\n"
        "Администратор рассмотрит её и даст доступ. Ожидайте."
    )

    if settings.admin_user_id:
        uname = f"@{message.from_user.username}" if message.from_user.username else "—"
        amo_info = f"AmoCRM ID: <code>{amocrm_user_id}</code>" if amocrm_user_id else "AmoCRM: не найден"
        text = (
            f"🔔 <b>Новая заявка на доступ</b>\n\n"
            f"Имя: {message.from_user.full_name or '—'}\n"
            f"Username: {uname}\n"
            f"ID: <code>{uid}</code>\n"
            f"Желаемая роль: 🛒 Сотрудник\n"
            f"Utel: <code>{utel_ext}</code>\n"
            f"{amo_info}\n\n"
            "Выберите роль:"
        )
        await send_to_user(settings.admin_user_id, text, reply_markup=approval_keyboard(uid))


# ---------------------------------------------------------------------------
# Шаг регистрации: выбор роли через inline-кнопки
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "reg_role:seller")
async def reg_role_seller(cq: CallbackQuery, state: FSMContext) -> None:
    role = await _get_role(cq.from_user.id)
    if role == "pending":
        await cq.answer("⏳ Ваша заявка уже на рассмотрении.", show_alert=True)
        return
    if role is not None:
        await cq.answer()
        return
    await state.set_state(Registration.waiting_utel_code)
    await cq.message.edit_text(
        "🛒 <b>Сотрудник</b>\n\n"
        "Введите ваш <b>код оператора Utel</b> (внутренний номер, например: <code>101</code>):"
    )
    await cq.answer()


@router.callback_query(F.data == "reg_role:manager")
async def reg_role_manager(cq: CallbackQuery, state: FSMContext) -> None:
    uid = cq.from_user.id
    role = await _get_role(uid)
    if role == "pending":
        await cq.answer("⏳ Ваша заявка уже на рассмотрении.", show_alert=True)
        return
    if role is not None:
        await cq.answer()
        return

    from app.bot.menu import approval_keyboard
    from app.config import settings
    from app.db import get_session
    from app.repository import create_pending_user
    from app.telegram import send_to_user

    async with get_session() as session:
        await create_pending_user(
            session,
            tg_user_id=uid,
            username=cq.from_user.username,
            full_name=cq.from_user.full_name,
        )

    await state.clear()
    await cq.message.edit_text(
        "👔 <b>Начальник</b>\n\n"
        "⏳ <b>Заявка отправлена</b>\n"
        "Администратор рассмотрит её и даст доступ. Ожидайте."
    )
    await cq.answer()

    if settings.admin_user_id:
        uname = f"@{cq.from_user.username}" if cq.from_user.username else "—"
        text = (
            f"🔔 <b>Новая заявка на доступ</b>\n\n"
            f"Имя: {cq.from_user.full_name or '—'}\n"
            f"Username: {uname}\n"
            f"ID: <code>{uid}</code>\n"
            f"Желаемая роль: 👔 Начальник\n\n"
            "Выберите роль:"
        )
        await send_to_user(settings.admin_user_id, text, reply_markup=approval_keyboard(uid))


# ---------------------------------------------------------------------------
# Хендлеры кнопок постоянного нижнего меню (ReplyKeyboard)
# ---------------------------------------------------------------------------

_PERIOD_BTNS: dict[str, tuple[str, str]] = {
    "📋 AmoCRM лиды":     ("amocrm",       "📋 <b>AmoCRM лиды</b>\n\nВыберите период:"),
    "📞 Звонки Utel":     ("utel",         "📞 <b>Звонки Utel</b>\n\nВыберите период:"),
    "👤 Сотрудники CRM":  ("amocrm_users", "👤 <b>Сотрудники AmoCRM</b>\n\nВыберите период:"),
    "👥 По операторам":   ("operators",    "👥 <b>По операторам</b>\n\nВыберите период:"),
    "🔴 Пропущенные":     ("missed",       "🔴 <b>Пропущенные звонки</b>\n\nВыберите период:"),
    "📞 Мои звонки":      ("my_utel",      "📞 <b>Мои звонки</b>\n\nВыберите период:"),
    "🔴 Мои пропущенные": ("my_missed",    "🔴 <b>Мои пропущенные</b>\n\nВыберите период:"),
    "📋 Мои лиды AmoCRM": ("my_amocrm",   "📋 <b>Мои лиды AmoCRM</b>\n\nВыберите период:"),
}


@router.message(F.text.in_(_PERIOD_BTNS))
async def handle_period_btn(message: Message) -> None:
    role = await _get_role(message.from_user.id)
    if not has_access(role, SELLER):
        return
    prefix, text = _PERIOD_BTNS[message.text]
    await message.answer(text, reply_markup=period_keyboard(prefix, back="menu:main"))


@router.message(F.text == "📈 Сравнения")
async def handle_compare_btn(message: Message) -> None:
    role = await _get_role(message.from_user.id)
    if not has_access(role, MANAGER):
        return
    await message.answer(
        "📈 <b>Сравнение периодов</b>\n\nВыберите тип данных:",
        reply_markup=compare_type_keyboard(),
    )


@router.message(F.text == "👥 Пользователи")
async def handle_users_btn(message: Message) -> None:
    role = await _get_role(message.from_user.id)
    if not has_access(role, ADMIN):
        return
    from app.db import get_session
    from app.repository import list_bot_users
    async with get_session() as session:
        users = await list_bot_users(session)
    if not users:
        await message.answer("👥 <b>Пользователи</b>\n\nСписок пуст.", reply_markup=users_list_keyboard([]))
    else:
        await message.answer(
            f"👥 <b>Пользователи</b> ({len(users)} чел.)\n\nНажмите 🗑 для удаления:",
            reply_markup=users_list_keyboard(users),
        )


# ---------------------------------------------------------------------------
# Навигация: главное меню
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "menu:main")
async def cb_main_menu(cq: CallbackQuery) -> None:
    role = await _get_role(cq.from_user.id)
    if not has_access(role, SELLER):
        await cq.answer("⛔ Нет доступа.", show_alert=True)
        return
    await cq.message.edit_text(MAIN_MENU_TEXT, reply_markup=main_menu_keyboard(role))
    await cq.answer()


@router.callback_query(F.data == "menu:amocrm")
async def cb_menu_amocrm(cq: CallbackQuery) -> None:
    if not await _check(cq, MANAGER):
        return
    await cq.message.edit_text(
        "📋 <b>AmoCRM лиды</b>\n\nВыберите период:",
        reply_markup=period_keyboard("amocrm"),
    )
    await cq.answer()


@router.callback_query(F.data == "menu:utel")
async def cb_menu_utel(cq: CallbackQuery) -> None:
    if not await _check(cq, MANAGER):
        return
    await cq.message.edit_text(
        "📞 <b>Звонки Utel</b>\n\nВыберите период:",
        reply_markup=period_keyboard("utel"),
    )
    await cq.answer()


@router.callback_query(F.data == "menu:amocrm_users")
async def cb_menu_amocrm_users(cq: CallbackQuery) -> None:
    if not await _check(cq, MANAGER):
        return
    await cq.message.edit_text(
        "👤 <b>Сотрудники AmoCRM</b>\n\nВыберите период:",
        reply_markup=period_keyboard("amocrm_users"),
    )
    await cq.answer()


@router.callback_query(F.data == "menu:operators")
async def cb_menu_operators(cq: CallbackQuery) -> None:
    if not await _check(cq, MANAGER):
        return
    await cq.message.edit_text(
        "👥 <b>По операторам</b>\n\nВыберите период:",
        reply_markup=period_keyboard("operators"),
    )
    await cq.answer()


@router.callback_query(F.data == "menu:missed")
async def cb_menu_missed(cq: CallbackQuery) -> None:
    if not await _check(cq, MANAGER):
        return
    await cq.message.edit_text(
        "🔴 <b>Пропущенные звонки</b>\n\nВыберите период:",
        reply_markup=period_keyboard("missed"),
    )
    await cq.answer()


@router.callback_query(F.data == "menu:compare_type")
async def cb_compare_type(cq: CallbackQuery) -> None:
    if not await _check(cq, MANAGER):
        return
    await cq.message.edit_text(
        "📈 <b>Сравнение периодов</b>\n\nВыберите тип данных:",
        reply_markup=compare_type_keyboard(),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("cmp_type:"))
async def cb_cmp_type_selected(cq: CallbackQuery) -> None:
    if not await _check(cq, MANAGER):
        return
    data_type = cq.data.split(":", 1)[1]
    labels = {"amocrm": "AmoCRM лиды", "utel": "Звонки Utel"}
    label = labels.get(data_type, data_type)
    await cq.message.edit_text(
        f"📈 <b>Сравнение — {label}</b>\n\nВыберите режим:",
        reply_markup=compare_period_keyboard(data_type),
    )
    await cq.answer()


# ---------------------------------------------------------------------------
# Отчёты по периодам (только manager/admin)
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("amocrm:"))
async def cb_amocrm_report(cq: CallbackQuery) -> None:
    if not await _check(cq, MANAGER):
        return
    period_key = cq.data.split(":", 1)[1]
    label = PERIOD_LABELS.get(period_key, period_key)
    await cq.message.edit_text(f"⏳ Загружаю AmoCRM данные ({label})…")
    await cq.answer()
    from_utc, to_utc = PERIOD_FUNCS[period_key]()
    from app.amocrm.reports import get_lead_metrics
    from app.formatting import format_amocrm_period_report
    metrics = await get_lead_metrics(from_utc, to_utc)
    error = metrics.get("error")
    text = f"❌ Ошибка AmoCRM: {error}" if error else format_amocrm_period_report(
        metrics, label, from_utc=from_utc, to_utc=to_utc
    )
    await cq.message.edit_text(text, reply_markup=period_keyboard("amocrm"))


@router.callback_query(F.data.startswith("utel:"))
async def cb_utel_report(cq: CallbackQuery) -> None:
    if not await _check(cq, MANAGER):
        return
    period_key = cq.data.split(":", 1)[1]
    label = PERIOD_LABELS.get(period_key, period_key)
    await cq.message.edit_text(f"⏳ Загружаю данные звонков ({label})…")
    await cq.answer()
    from_utc, to_utc = PERIOD_FUNCS[period_key]()
    from app.analytics_utel import get_period_stats
    from app.db import get_session
    from app.formatting import format_utel_period_report
    async with get_session() as session:
        stats = await get_period_stats(session, from_utc, to_utc)
    text = format_utel_period_report(stats, label)
    await cq.message.edit_text(text, reply_markup=period_keyboard("utel"))


@router.callback_query(F.data.startswith("amocrm_users:"))
async def cb_amocrm_users_report(cq: CallbackQuery) -> None:
    if not await _check(cq, MANAGER):
        return
    period_key = cq.data.split(":", 1)[1]
    label = PERIOD_LABELS.get(period_key, period_key)
    await cq.message.edit_text(f"⏳ Загружаю данные по сотрудникам ({label})…")
    await cq.answer()
    from_utc, to_utc = PERIOD_FUNCS[period_key]()
    from app.amocrm.reports import get_lead_metrics_by_users
    from app.formatting import format_amocrm_users_report
    result = await get_lead_metrics_by_users(from_utc, to_utc)
    error = result.get("error")
    text = f"❌ Ошибка AmoCRM: {error}" if error else format_amocrm_users_report(
        result, label, from_utc=from_utc, to_utc=to_utc
    )
    await cq.message.edit_text(text, reply_markup=period_keyboard("amocrm_users"))


@router.callback_query(F.data.startswith("operators:"))
async def cb_operators_report(cq: CallbackQuery) -> None:
    if not await _check(cq, MANAGER):
        return
    period_key = cq.data.split(":", 1)[1]
    label = PERIOD_LABELS.get(period_key, period_key)
    await cq.message.edit_text(f"⏳ Загружаю данные по операторам ({label})…")
    await cq.answer()
    from_utc, to_utc = PERIOD_FUNCS[period_key]()
    from app.analytics_utel import get_period_stats
    from app.db import get_session
    from app.formatting import format_utel_period_report
    async with get_session() as session:
        stats = await get_period_stats(session, from_utc, to_utc)
    text = format_utel_period_report(stats, label)
    await cq.message.edit_text(text, reply_markup=period_keyboard("operators"))


@router.callback_query(F.data.startswith("missed:"))
async def cb_missed_report(cq: CallbackQuery) -> None:
    if not await _check(cq, MANAGER):
        return
    period_key = cq.data.split(":", 1)[1]
    label = PERIOD_LABELS.get(period_key, period_key)
    await cq.message.edit_text(f"⏳ Загружаю пропущенные ({label})…")
    await cq.answer()
    from_utc, to_utc = PERIOD_FUNCS[period_key]()
    from app.analytics_utel import get_period_stats
    from app.db import get_session
    async with get_session() as session:
        stats = await get_period_stats(session, from_utc, to_utc)
    cb_rate = (
        f"{stats.callbacks_done}/{stats.callbacks_total} перезвонили"
        if stats.callbacks_total > 0 else "нет данных"
    )
    text = (
        f"🔴 <b>Пропущенные звонки — {label}</b>\n"
        "─────────────────────\n"
        f"Пропущено:   {stats.total_missed}\n"
        f"Перезвоны:   {cb_rate}\n"
        f"% пропусков: {stats.miss_rate}%"
    )
    await cq.message.edit_text(text, reply_markup=period_keyboard("missed"))


# ---------------------------------------------------------------------------
# Сравнения (только manager/admin)
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("compare:"))
async def cb_compare_report(cq: CallbackQuery) -> None:
    if not await _check(cq, MANAGER):
        return
    parts = cq.data.split(":")
    if len(parts) < 3:
        await cq.answer("Ошибка данных", show_alert=True)
        return
    data_type, compare_key = parts[1], parts[2]
    compare_label = COMPARE_LABELS.get(compare_key, compare_key)
    await cq.message.edit_text(f"⏳ Считаю {compare_label}…")
    await cq.answer()
    pair_func = COMPARE_FUNCS.get(compare_key)
    if not pair_func:
        await cq.message.edit_text("❌ Неизвестный режим сравнения")
        return
    (from_cur, to_cur), (from_prev, to_prev) = pair_func()
    label_cur, label_prev = _get_compare_labels(compare_key)
    if data_type == "amocrm":
        from app.amocrm.reports import get_lead_metrics
        from app.formatting import format_amocrm_compare_report
        metrics_cur = await get_lead_metrics(from_cur, to_cur)
        metrics_prev = await get_lead_metrics(from_prev, to_prev)
        if metrics_cur.get("error") or metrics_prev.get("error"):
            err = metrics_cur.get("error") or metrics_prev.get("error")
            text = f"❌ Ошибка AmoCRM: {err}"
        else:
            text = format_amocrm_compare_report(
                metrics_cur, metrics_prev, label_cur, label_prev,
                from_cur=from_cur, to_cur=to_cur,
                from_prev=from_prev, to_prev=to_prev,
            )
    elif data_type == "utel":
        from app.analytics_utel import get_period_stats
        from app.db import get_session
        from app.formatting import format_utel_compare_report
        async with get_session() as session:
            stats_cur = await get_period_stats(session, from_cur, to_cur)
            stats_prev = await get_period_stats(session, from_prev, to_prev)
        text = format_utel_compare_report(stats_cur, stats_prev, label_cur, label_prev)
    else:
        text = f"❌ Неизвестный тип данных: {data_type}"
    await cq.message.edit_text(text, reply_markup=compare_period_keyboard(data_type))


def _get_compare_labels(compare_key: str) -> tuple[str, str]:
    return {
        "d2d": ("Сегодня", "Вчера"),
        "w2w": ("Эта неделя", "Прошлая неделя"),
        "m2m": ("Этот месяц", "Прошлый месяц"),
        "q2q": ("Этот квартал", "Прошлый квартал"),
        "y2y": ("Этот год", "Прошлый год"),
    }.get(compare_key, ("Текущий", "Предыдущий"))


# ---------------------------------------------------------------------------
# Меню продавца
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "menu:my_utel")
async def cb_menu_my_utel(cq: CallbackQuery) -> None:
    if not await _check(cq, SELLER):
        return
    await cq.message.edit_text(
        "📞 <b>Мои звонки</b>\n\nВыберите период:",
        reply_markup=period_keyboard("my_utel", back="menu:main"),
    )
    await cq.answer()


@router.callback_query(F.data == "menu:my_missed")
async def cb_menu_my_missed(cq: CallbackQuery) -> None:
    if not await _check(cq, SELLER):
        return
    await cq.message.edit_text(
        "🔴 <b>Мои пропущенные</b>\n\nВыберите период:",
        reply_markup=period_keyboard("my_missed", back="menu:main"),
    )
    await cq.answer()


@router.callback_query(F.data == "menu:my_amocrm")
async def cb_menu_my_amocrm(cq: CallbackQuery) -> None:
    if not await _check(cq, SELLER):
        return
    await cq.message.edit_text(
        "📋 <b>Мои лиды AmoCRM</b>\n\nВыберите период:",
        reply_markup=period_keyboard("my_amocrm", back="menu:main"),
    )
    await cq.answer()


# ---------------------------------------------------------------------------
# Отчёты продавца
# ---------------------------------------------------------------------------

async def _get_seller_user(uid: int):
    """Возвращает BotUser продавца или None."""
    from app.db import get_session
    from app.repository import get_bot_user
    async with get_session() as session:
        return await get_bot_user(session, uid)


@router.callback_query(F.data.startswith("my_utel:"))
async def cb_my_utel_report(cq: CallbackQuery) -> None:
    if not await _check(cq, SELLER):
        return
    period_key = cq.data.split(":", 1)[1]
    label = PERIOD_LABELS.get(period_key, period_key)
    await cq.message.edit_text(f"⏳ Загружаю ваши звонки ({label})…")
    await cq.answer()

    user = await _get_seller_user(cq.from_user.id)
    if not user or not user.utel_ext:
        await cq.message.edit_text(
            "⚠️ Utel-номер не привязан. Обратитесь к администратору.",
            reply_markup=period_keyboard("my_utel", back="menu:main"),
        )
        return

    from_utc, to_utc = PERIOD_FUNCS[period_key]()
    from app.analytics_utel import get_period_stats
    from app.db import get_session
    from app.formatting import format_utel_period_report
    async with get_session() as session:
        stats = await get_period_stats(session, from_utc, to_utc, operator_ext=user.utel_ext)
    text = format_utel_period_report(stats, f"{label} (мои)")
    await cq.message.edit_text(text, reply_markup=period_keyboard("my_utel", back="menu:main"))


@router.callback_query(F.data.startswith("my_missed:"))
async def cb_my_missed_report(cq: CallbackQuery) -> None:
    if not await _check(cq, SELLER):
        return
    period_key = cq.data.split(":", 1)[1]
    label = PERIOD_LABELS.get(period_key, period_key)
    await cq.message.edit_text(f"⏳ Загружаю ваши пропущенные ({label})…")
    await cq.answer()

    user = await _get_seller_user(cq.from_user.id)
    if not user or not user.utel_ext:
        await cq.message.edit_text(
            "⚠️ Utel-номер не привязан. Обратитесь к администратору.",
            reply_markup=period_keyboard("my_missed", back="menu:main"),
        )
        return

    from_utc, to_utc = PERIOD_FUNCS[period_key]()
    from app.analytics_utel import get_period_stats
    from app.db import get_session
    async with get_session() as session:
        stats = await get_period_stats(session, from_utc, to_utc, operator_ext=user.utel_ext)

    cb_rate = (
        f"{stats.callbacks_done}/{stats.callbacks_total} перезвонили"
        if stats.callbacks_total > 0 else "нет данных"
    )
    text = (
        f"🔴 <b>Мои пропущенные — {label}</b>\n"
        "─────────────────────\n"
        f"Пропущено:   {stats.total_missed}\n"
        f"Перезвоны:   {cb_rate}\n"
        f"% пропусков: {stats.miss_rate}%"
    )
    await cq.message.edit_text(text, reply_markup=period_keyboard("my_missed", back="menu:main"))


@router.callback_query(F.data.startswith("my_amocrm:"))
async def cb_my_amocrm_report(cq: CallbackQuery) -> None:
    if not await _check(cq, SELLER):
        return
    period_key = cq.data.split(":", 1)[1]
    label = PERIOD_LABELS.get(period_key, period_key)
    await cq.message.edit_text(f"⏳ Загружаю ваши лиды ({label})…")
    await cq.answer()

    user = await _get_seller_user(cq.from_user.id)
    if not user or not user.amocrm_user_id:
        await cq.message.edit_text(
            "⚠️ AmoCRM-аккаунт не привязан. Обратитесь к администратору.",
            reply_markup=period_keyboard("my_amocrm", back="menu:main"),
        )
        return

    from_utc, to_utc = PERIOD_FUNCS[period_key]()
    from app.amocrm.reports import get_lead_metrics
    from app.formatting import format_amocrm_period_report
    metrics = await get_lead_metrics(from_utc, to_utc, responsible_user_id=user.amocrm_user_id)
    error = metrics.get("error")
    text = f"❌ Ошибка AmoCRM: {error}" if error else format_amocrm_period_report(
        metrics, f"{label} (мои)", from_utc=from_utc, to_utc=to_utc
    )
    await cq.message.edit_text(text, reply_markup=period_keyboard("my_amocrm", back="menu:main"))


# ---------------------------------------------------------------------------
# Кнопка «Перезвонил» (все авторизованные роли)
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("callback_done:"))
async def cb_callback_done(cq: CallbackQuery) -> None:
    role = await _get_role(cq.from_user.id)
    if not has_access(role, SELLER):
        await cq.answer("⛔ Нет доступа.", show_alert=True)
        return

    tracking_id = cq.data.split(":", 1)[1]
    from app.db import get_session
    from app.repository import get_tracking_by_id, mark_called_back
    async with get_session() as session:
        tracking = await get_tracking_by_id(session, tracking_id)
        if tracking is None:
            await cq.answer("Запись не найдена.", show_alert=True)
            return
        if tracking.called_back:
            await cq.answer("Уже отмечено как перезвоненное.", show_alert=True)
            return
        user = cq.from_user
        by_name = user.full_name or user.username or str(user.id)
        await mark_called_back(session, tracking, called_back_by=by_name, manual=True)

    original_text = cq.message.text or cq.message.caption or ""
    new_text = original_text + f"\n\n✅ Перезвонил: {by_name}"
    try:
        await cq.message.edit_text(new_text)
    except Exception:
        pass
    await cq.answer("✅ Отмечено!")
    logger.info("Manual callback marked for tracking_id=%s by %s", tracking_id, by_name)
