from __future__ import annotations

from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # --- Telegram ---
    bot_token: str
    telegram_chat_id: int
    telegram_thread_id: Optional[int] = None
    # Главный администратор бота (ваш Telegram user_id).
    # Узнать через команду /myid в боте после первого запуска.
    admin_user_id: int = 0

    @field_validator("telegram_thread_id", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: object) -> object:
        if v == "":
            return None
        return v

    # Whitelist user_id для доступа к отчётам (через запятую: "123456,789012")
    # Если пусто — доступ открыт всем (для начала). Рекомендуется заполнить.
    report_allowed_users: str = ""

    # --- Webhook security ---
    webhook_secret: str = ""
    webhook_secret_header: str = "X-Webhook-Secret"

    # --- Localisation ---
    timezone: str = "Asia/Tashkent"

    # --- App ---
    log_level: str = "INFO"
    port: int = 8000

    @field_validator("log_level")
    @classmethod
    def normalise_log_level(cls, v: str) -> str:
        return v.upper()

    # --- База данных ---
    # sqlite+aiosqlite:///./data/calls.db (по умолчанию)
    database_url: str = ""

    # --- Планировщик перезвонов ---
    callback_check_minutes: int = 10  # как часто проверять (и порог эскалации)

    # --- Операторы ---
    # JSON-маппинг extension → имя: {"101": "Азиз", "102": "Камола"}
    operators_map: str = ""
    # JSON-маппинг extension → AmoCRM user_id: {"101": 123456, "102": 789012}
    utel_amocrm_map: str = ""

    # --- AmoCRM ---
    amocrm_subdomain: str = ""
    amocrm_client_id: str = ""
    amocrm_client_secret: str = ""
    amocrm_redirect_uri: str = ""
    # Долгосрочный токен (если задан — OAuth и refresh не нужны)
    amocrm_long_lived_token: str = ""
    # Статус ID воронки, который считается «необработанным» (первый статус).
    # Если пустой — используется первый статус из API /leads/pipelines.
    amocrm_initial_status_id: int = 0

    # --- BILLZ POS ---
    billz_api_url: str = "https://api-admin.billz.ai"
    billz_secret: str = ""
    billz_company_id: str = ""
    billz_platform_id: str = "7d4a4c38-dd84-4902-b744-0488b80a4c01"
    # UUID магазинов через запятую (используется как shop_ids в report-эндпоинтах).
    # Если пусто — отчёты без фильтра по магазину.
    billz_shop_ids: str = ""
    billz_currency: str = "UZS"
    billz_digest_hour: int = 9     # час отправки ежедневного BILLZ-дайджеста

    # --- Hermes AI Agent ---
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    hermes_digest_hour: int = 9       # час отправки утреннего дайджеста (локальный timezone)
    hermes_audit_ttl_hours: int = 4   # TTL кэша аудита в часах

    def utel_to_amocrm(self) -> dict[str, int]:
        """Возвращает маппинг utel_ext → amocrm_user_id из UTEL_AMOCRM_MAP."""
        if not self.utel_amocrm_map.strip():
            return {}
        import json
        try:
            raw = json.loads(self.utel_amocrm_map)
            return {str(k): int(v) for k, v in raw.items()}
        except Exception:
            return {}

    def allowed_user_ids(self) -> set[int]:
        """Возвращает set разрешённых user_id из строки через запятую."""
        if not self.report_allowed_users.strip():
            return set()  # пустой = все разрешены
        ids = set()
        for part in self.report_allowed_users.split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
        return ids


settings = Settings()
