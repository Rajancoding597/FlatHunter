import json
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str
    admin_telegram_ids: List[int]
    supabase_url: str
    supabase_service_key: str
    gemini_api_key: str = ""
    groq_api_key: str = ""
    llm_provider: str = "groq"  # "groq" or "gemini"
    vision_provider: str = "gemini"  # independently select "groq" or "gemini"
    groq_vision_model: str = "qwen/qwen3.6-27b"
    gemini_vision_model: str = "gemini-2.5-flash-lite"

    flathunter_default_city: str = "Hyderabad"
    flathunter_default_timezone: str = "Asia/Kolkata"
    listing_stale_after_days: int = 7
    follow_up_after_hours: int = 24
    max_active_searches: int = 1

    # Email settings (optional, can be empty string if not provided)
    smtp_server: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    imap_server: str = ""
    imap_port: int = 993

    # Disable automatic JSON decoding so the documented comma-separated format
    # and JSON-list format can both be handled by one explicit validator.
    model_config = SettingsConfigDict(env_file=".env.local", env_file_encoding="utf-8", enable_decoding=False)

    @field_validator("admin_telegram_ids", mode="before")
    @classmethod
    def parse_admin_telegram_ids(cls, value):
        if isinstance(value, list):
            return value
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if stripped.startswith("["):
            return json.loads(stripped)
        return [int(user_id.strip()) for user_id in stripped.split(",") if user_id.strip()]


settings = Settings()
