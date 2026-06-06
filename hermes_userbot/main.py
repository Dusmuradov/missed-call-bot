import asyncio
import logging

from pyrogram import filters
from pyrogram.types import Message

from hermes_userbot.autoreply import handle_incoming, should_autoreply
from hermes_userbot.client import create_userbot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    app = create_userbot()

    @app.on_message(filters.private & filters.incoming)
    async def on_message(client, message: Message):
        if not should_autoreply(message):
            return
        try:
            await handle_incoming(client, message)
        except Exception as exc:
            logger.exception("Autoreply failed: %s", exc)

    logger.info("Hermes userbot starting...")
    await app.start()
    logger.info("Hermes userbot ready.")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
