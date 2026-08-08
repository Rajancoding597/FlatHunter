from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    telegram_bot_token: str
    admin_telegram_ids: List[int]
    supabase_url: str
    supabase_service_key: str
    gemini_api_key: str = ""
    groq_api_key: str = ""
    llm_provider: str = "groq"  # "groq" or "gemini"
    
    flathunter_default_city: str = "Hyderabad"
    flathunter_default_timezone: str = "Asia/Kolkata"
    listing_stale_after_days: int = 7
    follow_up_after_hours: int = 24
    
    # Email settings (optional, can be empty string if not provided)
    smtp_server: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    imap_server: str = ""
    imap_port: int = 993

    model_config = SettingsConfigDict(env_file=".env.local", env_file_encoding="utf-8")

settings = Settings()
