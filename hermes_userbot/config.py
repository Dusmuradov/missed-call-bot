from pydantic_settings import BaseSettings


class UserbotSettings(BaseSettings):
    api_id: int = 0
    api_hash: str = ""
    session_string: str = ""
    autoreply_mode: str = "suggest"
    main_bot_id: int = 0
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    autoreply_timeout_minutes: int = 5

    class Config:
        env_file = ".env"
        case_sensitive = False


userbot_settings = UserbotSettings()
