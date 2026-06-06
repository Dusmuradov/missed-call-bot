from pyrogram import Client

from hermes_userbot.config import userbot_settings


def create_userbot() -> Client:
    return Client(
        name="hermes_userbot",
        api_id=userbot_settings.api_id,
        api_hash=userbot_settings.api_hash,
        session_string=userbot_settings.session_string,
        in_memory=True,
    )
