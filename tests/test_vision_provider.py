import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_TELEGRAM_IDS", "1,2")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from app.ingestion.schemas import FlatHunterExtractionV1
from app.vision.errors import VisionConfigurationError, VisionJSONModeError
from app.vision.factory import get_vision_provider
from app.vision.providers import GeminiVisionProvider, GroqVisionProvider, VisionImage


def configured(provider: str, *, groq_key: str = "groq-test", gemini_key: str = "gemini-test"):
    return SimpleNamespace(
        vision_provider=provider,
        groq_api_key=groq_key,
        gemini_api_key=gemini_key,
        groq_vision_model="qwen/qwen3.6-27b",
        gemini_vision_model="gemini-test-model",
    )


def test_factory_selects_groq():
    provider = get_vision_provider(configured("groq"))
    assert isinstance(provider, GroqVisionProvider)
    assert provider.model_name == "qwen/qwen3.6-27b"


def test_factory_selects_gemini():
    provider = get_vision_provider(configured("gemini"))
    assert isinstance(provider, GeminiVisionProvider)
    assert provider.model_name == "gemini-test-model"


@pytest.mark.parametrize(
    ("provider", "groq_key", "gemini_key", "expected"),
    [("groq", "", "unused", "GROQ_API_KEY"), ("gemini", "unused", "", "GEMINI_API_KEY")],
)
def test_factory_rejects_missing_selected_provider_key(provider, groq_key, gemini_key, expected):
    with pytest.raises(VisionConfigurationError, match=expected):
        get_vision_provider(configured(provider, groq_key=groq_key, gemini_key=gemini_key))


def test_factory_rejects_unknown_provider():
    with pytest.raises(VisionConfigurationError, match="Unsupported VISION_PROVIDER"):
        get_vision_provider(configured("randomthing"))


def valid_extraction_json() -> str:
    return json.dumps({
        "content_type": "PROPERTY_LISTING",
        "canonical": {
            "listing_type": "PRIVATE_ROOM", "property_configuration": "3BHK", "city": "Hyderabad",
            "locality": "Kondapur", "location_text": None, "landmark": None, "rent": 15833,
            "maintenance": None, "deposit": 25333, "brokerage": 6000, "available_from": "2026-09-01",
            "furnishing": "SEMI_FURNISHED", "attached_bathroom": True, "car_parking": None, "bike_parking": None,
        },
        "contacts": [],
        "additional_attributes": {"monthly_costs": [{"type": "UTILITIES", "amount": 663}]},
        "conflicts": [], "uncertain_fields": [], "extraction_notes": [],
    })


class FakeGroqCompletions:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response
        self.error = error

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeGroqClient:
    def __init__(self, response=None, error=None):
        self.chat = SimpleNamespace(completions=FakeGroqCompletions(response=response, error=error))


@pytest.mark.asyncio
async def test_groq_sends_two_images_and_admin_text_in_one_request():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=valid_extraction_json()))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        request_id="request-1",
    )
    client = FakeGroqClient(response=response)
    provider = GroqVisionProvider(api_key="test", client=client)

    extracted = await provider.extract_listing(
        images=[VisionImage(b"first"), VisionImage(b"second")],
        text_inputs=["Original rent 15,833"],
        admin_notes=["Rent is actually 15,833."],
    )

    assert extracted.canonical.listing_type == "PRIVATE_ROOM"
    assert len(client.chat.completions.calls) == 1
    content = client.chat.completions.calls[0]["messages"][0]["content"]
    assert len([item for item in content if item["type"] == "image_url"]) == 2
    assert "Rent is actually 15,833" in content[0]["text"]


@pytest.mark.asyncio
async def test_json_validate_failed_is_typed_separately():
    error = RuntimeError("json failed")
    error.body = {"error": {"code": "json_validate_failed", "message": "Failed to validate JSON"}}
    provider = GroqVisionProvider(api_key="test", client=FakeGroqClient(error=error))

    with pytest.raises(VisionJSONModeError, match="server-side validation failed"):
        await provider.extract_listing(images=[VisionImage(b"image")])


def test_extraction_schema_preserves_unknowns_and_noncanonical_costs():
    parsed = FlatHunterExtractionV1.model_validate_json(valid_extraction_json())
    assert parsed.canonical.maintenance is None
    assert parsed.canonical.car_parking is None
    assert parsed.additional_attributes["monthly_costs"][0]["amount"] == 663


def test_extraction_schema_rejects_name_only_contact_without_explicit_channel():
    payload = json.loads(valid_extraction_json())
    payload["contacts"] = [{"name": "Visible profile name", "role": "UNKNOWN", "channels": []}]

    with pytest.raises(ValueError, match="at least 1 item"):
        FlatHunterExtractionV1.model_validate(payload)
