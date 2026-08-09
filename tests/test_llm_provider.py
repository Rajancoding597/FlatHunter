import os

import pytest
from pydantic import ValidationError

# Configure settings before importing the application config module.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_TELEGRAM_IDS", "1,2")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from app.llm import gemini


def test_unknown_llm_provider_is_rejected(monkeypatch):
    monkeypatch.setattr(gemini.settings, "llm_provider", "unsupported")

    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        gemini.get_llm_provider()

def test_admin_ids_accept_documented_comma_separated_format():
    from app.config import Settings

    parsed = Settings(
        telegram_bot_token="test-token",
        admin_telegram_ids="3, 4",
        supabase_url="https://example.supabase.co",
        supabase_service_key="test-service-key",
    )

    assert parsed.admin_telegram_ids == [3, 4]


def test_runtime_observability_settings_have_safe_defaults():
    from app.config import Settings

    parsed = Settings(
        telegram_bot_token="test-token",
        admin_telegram_ids="3, 4",
        supabase_url="https://example.supabase.co",
        supabase_service_key="test-service-key",
        app_build_sha=" ",
    )

    assert parsed.app_build_sha == "dev"
    assert parsed.renter_collection_mode == "hybrid"


def test_renter_collection_mode_is_normalized_and_validated():
    from app.config import Settings

    parsed = Settings(
        telegram_bot_token="test-token",
        admin_telegram_ids="3, 4",
        supabase_url="https://example.supabase.co",
        supabase_service_key="test-service-key",
        renter_collection_mode=" GUIDED ",
    )
    assert parsed.renter_collection_mode == "guided"

    with pytest.raises(ValidationError, match="renter_collection_mode"):
        Settings(
            telegram_bot_token="test-token",
            admin_telegram_ids="3, 4",
            supabase_url="https://example.supabase.co",
            supabase_service_key="test-service-key",
            renter_collection_mode="autonomous",
        )
