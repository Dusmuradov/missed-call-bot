"""
Telegram-клиент: синглтон Bot, Dispatcher для inline-меню, send_notification.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup

from app.config import settings

logger = logging.getLogger(__name__)

# Единственный экземпляр Bot на весь процесс
_bot: Optional[Bot] = None
_dp: Optional[Dispatcher] = None
_polling_task: Optional[asyncio.Task] = None


def get_bot() -> Bot:
    """Возвращает синглтон Bot, создавая его при первом вызове."""
    global _bot
    if _bot is None:
        _bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _bot


def get_dispatcher() -> Dispatcher:
    """Возвращает синглтон Dispatcher с подключёнными роутерами."""
    global _dp
    if _dp is None:
        from app.bot.admin import router as admin_router
        from app.bot.handlers import router
        from app.bot.hermes_handlers import router as hermes_router
        _dp = Dispatcher(storage=MemoryStorage())
        _dp.include_router(admin_router)   # admin раньше — приоритет на approve:/reject:
        _dp.include_router(router)
        _dp.include_router(hermes_router)  # hermes последним — free-form F.text
    return _dp


async def start_polling() -> None:
    """Запускает long-polling в фоновой asyncio-задаче."""
    global _polling_task
    if _polling_task is not None and not _polling_task.done():
        return  # уже запущен

    bot = get_bot()
    dp = get_dispatcher()

    # Удаляем webhook если был (Railway может оставить его после смены конфигурации)
    await bot.delete_webhook(drop_pending_updates=True)

    async def _run():
        try:
            logger.info("Bot polling started.")
            await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("Bot polling error: %s", exc)

    _polling_task = asyncio.create_task(_run())


async def stop_polling() -> None:
    """Останавливает long-polling."""
    global _polling_task
    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
        try:
            await _polling_task
        except asyncio.CancelledError:
            pass
        _polling_task = None
    logger.info("Bot polling stopped.")


async def close_bot() -> None:
    """Закрывает HTTP-сессию бота. Вызывается при остановке приложения."""
    global _bot
    if _bot is not None:
        await _bot.session.close()
        _bot = None
        logger.info("Bot session closed.")


async def send_notification(text: str) -> bool:
    """
    Отправляет HTML-сообщение в группу операторов.
    Возвращает True при успехе, False при ошибке.
    """
    bot = get_bot()
    try:
        await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=text,
            message_thread_id=settings.telegram_thread_id,
        )
        logger.info("Notification sent to chat_id=%s", settings.telegram_chat_id)
        return True
    except TelegramAPIError as exc:
        logger.error("Telegram API error: %s", exc)
        return False
    except Exception as exc:
        logger.exception("Unexpected error sending Telegram message: %s", exc)
        return False


async def send_to_user(
    uid: int, text: str, reply_markup=None
) -> bool:
    """Отправляет личное сообщение конкретному пользователю (для заявок и уведомлений)."""
    bot = get_bot()
    try:
        await bot.send_message(chat_id=uid, text=text, reply_markup=reply_markup)
        return True
    except TelegramAPIError as exc:
        logger.warning("send_to_user(%s) failed: %s", uid, exc)
        return False
    except Exception as exc:
        logger.exception("send_to_user(%s) unexpected error: %s", uid, exc)
        return False


async def send_escalation(text: str, tracking_id: str) -> Optional[int]:
    """
    Отправляет эскалационное сообщение с кнопкой «Перезвонил».
    Возвращает message_id для последующего редактирования.
    """
    from app.bot.menu import callback_done_keyboard

    bot = get_bot()
    try:
        msg = await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=text,
            message_thread_id=settings.telegram_thread_id,
            reply_markup=callback_done_keyboard(tracking_id),
        )
        return msg.message_id
    except TelegramAPIError as exc:
        logger.error("Telegram escalation error: %s", exc)
        return None
    except Exception as exc:
        logger.exception("Unexpected error sending escalation: %s", exc)
        return None
