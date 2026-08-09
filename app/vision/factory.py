"""Deterministic vision-provider resolution from FlatHunter settings."""

from __future__ import annotations

from typing import Any

from app.config import settings as application_settings
from app.vision.errors import VisionConfigurationError
from app.vision.providers import GeminiVisionProvider, GroqVisionProvider, VisionProvider


def get_vision_provider(settings: Any = None) -> VisionProvider:
    configured = settings or application_settings
    provider = configured.vision_provider.strip().lower()
    if provider == "groq":
        if not configured.groq_api_key.strip():
            raise VisionConfigurationError("GROQ_API_KEY is required when VISION_PROVIDER=groq.")
        return GroqVisionProvider(api_key=configured.groq_api_key, model_name=configured.groq_vision_model)
    if provider == "gemini":
        if not configured.gemini_api_key.strip():
            raise VisionConfigurationError("GEMINI_API_KEY is required when VISION_PROVIDER=gemini.")
        return GeminiVisionProvider(api_key=configured.gemini_api_key, model_name=configured.gemini_vision_model)
    raise VisionConfigurationError(
        f"Unsupported VISION_PROVIDER={configured.vision_provider!r}. Supported values are: groq, gemini."
    )
