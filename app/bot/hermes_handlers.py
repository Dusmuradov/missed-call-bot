from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.auth import SELLER, has_access
from app.db import get_session
from app.repository import get_bot_user

logger = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")


async def _get_user_with_amo(message: Message):
    """Возвращает BotUser с amocrm_user_id или отправляет ошибку и возвращает None."""
    tg_id = message.from_user.id
    async with get_session() as session:
        user = await get_bot_user(session, tg_id)

    if user is None or not has_access(user.role, SELLER):
        await message.answer("Нет доступа.")
        return None

    if not user.amocrm_user_id:
        await message.answer("Твой аккаунт не привязан к AmoCRM. Обратись к администратору.")
        return None

    return user


@router.message(Command("hot"))
async def cmd_hot(message: Message) -> None:
    """Топ горячих и тёплых сделок менеджера."""
    user = await _get_user_with_amo(message)
    if user is None:
        return

    wait = await message.answer("Анализирую твои сделки…")

    try:
        from hermes.audit import run_audit
        from hermes.digest import format_top_hot

        deals = await run_audit(user.amocrm_user_id, message.from_user.id, with_suggestions=True)

        if not deals:
            await wait.edit_text("Нет активных сделок.")
            return

        text = format_top_hot(deals, top_n=7)
        await wait.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)

    except Exception as exc:
        logger.exception("cmd_hot failed for user=%d: %s", message.from_user.id, exc)
        await wait.edit_text("Ошибка при анализе сделок. Попробуй позже.")


@router.message(Command("tasks"))
async def cmd_tasks(message: Message) -> None:
    """Утренний дайджест по запросу."""
    user = await _get_user_with_amo(message)
    if user is None:
        return

    wait = await message.answer("Готовлю список задач…")

    try:
        from hermes.audit import run_audit
        from hermes.digest import format_digest

        deals = await run_audit(user.amocrm_user_id, message.from_user.id, with_suggestions=False)

        if not deals:
            await wait.edit_text("Нет активных сделок.")
            return

        name = message.from_user.first_name or "Менеджер"
        text = format_digest(name, deals, top_n=5)
        await wait.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)

    except Exception as exc:
        logger.exception("cmd_tasks failed for user=%d: %s", message.from_user.id, exc)
        await wait.edit_text("Ошибка при загрузке задач. Попробуй позже.")


@router.message(Command("refresh"))
async def cmd_refresh(message: Message) -> None:
    """Принудительно обновить кэш аудита."""
    user = await _get_user_with_amo(message)
    if user is None:
        return

    wait = await message.answer("Обновляю данные из AmoCRM…")

    try:
        from hermes.audit import run_audit
        from hermes.digest import format_top_hot

        deals = await run_audit(
            user.amocrm_user_id, message.from_user.id,
            force_refresh=True, with_suggestions=True,
        )

        if not deals:
            await wait.edit_text("Нет активных сделок.")
            return

        text = format_top_hot(deals, top_n=7)
        await wait.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)

    except Exception as exc:
        logger.exception("cmd_refresh failed for user=%d: %s", message.from_user.id, exc)
        await wait.edit_text("Ошибка обновления.")


@router.message(Command("client"))
async def cmd_client(message: Message) -> None:
    """/client <имя> — анализ конкретной сделки."""
    user = await _get_user_with_amo(message)
    if user is None:
        return

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer("Укажи имя клиента: /client Иванов")
        return

    client_name = args[1].strip()
    wait = await message.answer(f"Ищу сделки по «{client_name}»…")

    try:
        from hermes.audit import run_audit
        from hermes.digest import format_deal_card

        deals = await run_audit(user.amocrm_user_id, message.from_user.id, with_suggestions=True)
        matched = [
            d for d in deals
            if client_name.lower() in (d.lead_name or "").lower()
            or client_name.lower() in (d.contact_name or "").lower()
        ]

        if not matched:
            await wait.edit_text(f"Сделки с «{client_name}» не найдены.")
            return

        cards = [format_deal_card(d) for d in matched[:3]]
        await wait.edit_text("\n\n".join(cards), parse_mode="HTML", disable_web_page_preview=True)

    except Exception as exc:
        logger.exception("cmd_client failed for user=%d: %s", message.from_user.id, exc)
        await wait.edit_text("Ошибка поиска.")


@router.message(Command("newchat"))
async def cmd_newchat(message: Message) -> None:
    """Очистить историю диалога с агентом."""
    from hermes.context import clear_history
    count = await clear_history(message.from_user.id)
    await message.answer(f"История очищена ({count} сообщений).")


@router.message(F.text & ~F.text.startswith("/"))
async def hermes_freeform(message: Message) -> None:
    """Свободный вопрос менеджера → агент Hermes."""
    tg_id = message.from_user.id
    async with get_session() as session:
        user = await get_bot_user(session, tg_id)

    if user is None or not has_access(user.role, SELLER):
        return

    try:
        from app.config import settings

        if not settings.deepseek_api_key:
            return

        wait = await message.answer("Думаю…")

        from hermes.agent import ask

        answer = await ask(
            tg_user_id=tg_id,
            user_text=message.text,
            amocrm_user_id=user.amocrm_user_id,
            role=user.role or "employee",
        )
        await wait.edit_text(answer, parse_mode="HTML", disable_web_page_preview=True)

    except Exception as exc:
        logger.exception("hermes_freeform failed for user=%d: %s", tg_id, exc)
