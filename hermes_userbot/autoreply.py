from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI
from pyrogram.types import Message

from hermes_userbot.config import userbot_settings

logger = logging.getLogger(__name__)


def should_autoreply(message: Message) -> bool:
    if message.chat.type.name != "PRIVATE":
        return False
    if message.from_user and message.from_user.is_self:
        return False
    if not (message.text or message.caption):
        return False
    return True


async def generate_reply(client_message: str, lead_context: dict | None) -> str:
    openai_client = AsyncOpenAI(
        api_key=userbot_settings.deepseek_api_key,
        base_url=userbot_settings.deepseek_base_url,
    )
    context_str = json.dumps(lead_context, ensure_ascii=False) if lead_context else "нет данных"
    system = (
        "Ты вежливый менеджер по продажам. Отвечаешь от имени менеджера клиенту.\n"
        "Будь кратким (2-3 предложения). Не раскрывай что ты AI.\n"
        f"Контекст сделки: {context_str}"
    )
    response = await openai_client.chat.completions.create(
        model=userbot_settings.deepseek_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": client_message},
        ],
        temperature=0.5,
        max_tokens=200,
    )
    return response.choices[0].message.content or ""


async def handle_incoming(client, message: Message) -> None:
    import asyncio

    text = message.text or message.caption or ""
    if not text:
        return

    reply = await generate_reply(text, lead_context=None)

    if userbot_settings.autoreply_mode == "autopilot":
        await message.reply(reply)
    else:
        # suggest: уведомить основной бот, подождать таймаут, потом ответить
        if userbot_settings.main_bot_id:
            sender = message.from_user.first_name if message.from_user else "Клиент"
            try:
                await client.send_message(
                    userbot_settings.main_bot_id,
                    f" <b>Входящее от {sender}</b>\n"
                    f"Сообщение: {text[:300]}\n\n"
                    f"Предлагаемый ответ:\n{reply}",
                )
            except Exception as exc:
                logger.warning("Failed to notify main bot: %s", exc)
        await asyncio.sleep(userbot_settings.autoreply_timeout_minutes * 60)
        await message.reply(reply)
