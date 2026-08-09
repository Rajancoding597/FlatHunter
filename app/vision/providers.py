"""Groq and Gemini implementations of the FlatHunter vision boundary."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from pydantic import ValidationError

from app.common.tracer import tracer
from app.ingestion.schemas import FlatHunterExtractionV1
from app.vision.errors import VisionInputError, VisionJSONModeError, VisionProviderError, VisionValidationError
from app.vision.prompt import build_extraction_prompt

logger = logging.getLogger(__name__)
SUPPORTED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


@dataclass(frozen=True)
class VisionImage:
    data: bytes
    mime_type: str = "image/jpeg"
    source_id: str | None = None


class VisionProvider(Protocol):
    provider_name: str
    model_name: str
    last_metadata: dict[str, Any]

    async def extract_listing(
        self,
        *,
        images: Sequence[VisionImage],
        text_inputs: Sequence[str] | None = None,
        admin_notes: Sequence[str] | None = None,
    ) -> FlatHunterExtractionV1: ...


class _BaseVisionProvider:
    max_images = 5

    def __init__(self, *, model_name: str) -> None:
        self.model_name = model_name
        self.last_metadata: dict[str, Any] = {}

    def _validate_images(self, images: Sequence[VisionImage]) -> None:
        if len(images) > self.max_images:
            raise VisionInputError(
                f"{self.provider_name} model {self.model_name} accepts at most {self.max_images} information images per property; received {len(images)}."
            )
        for image in images:
            if not image.data:
                raise VisionInputError("Information images must not be empty.")
            if image.mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
                raise VisionInputError(f"Unsupported information image MIME type: {image.mime_type}")

    def _validated_result(self, raw_text: str) -> FlatHunterExtractionV1:
        try:
            return FlatHunterExtractionV1.model_validate_json(raw_text)
        except (ValidationError, ValueError, json.JSONDecodeError) as error:
            raise VisionValidationError(
                f"{self.provider_name} returned data that does not validate as FlatHunterExtractionV1: {error}",
                provider=self.provider_name,
                code="invalid_extraction",
            ) from error

    def _trace(self, *, image_count: int, latency_ms: float, success: bool, validation_success: bool, error: str | None = None) -> None:
        tracer.log_event(
            event_type="VISION_EXTRACTION",
            direction="OUTBOUND",
            latency_ms=latency_ms,
            status="SUCCESS" if success else "ERROR",
            error=error,
            payload={
                "provider": self.provider_name,
                "model": self.model_name,
                "task": "listing_extraction",
                "input_image_count": image_count,
                "validation_success": validation_success,
                **self.last_metadata,
            },
        )


class GroqVisionProvider(_BaseVisionProvider):
    provider_name = "groq"

    def __init__(self, *, api_key: str, model_name: str = "qwen/qwen3.6-27b", client: Any = None) -> None:
        super().__init__(model_name=model_name)
        if not api_key.strip():
            raise ValueError("GROQ_API_KEY is required when VISION_PROVIDER=groq.")
        if client is None:
            from groq import Groq
            client = Groq(api_key=api_key)
        self.client = client

    async def extract_listing(
        self, *, images: Sequence[VisionImage], text_inputs: Sequence[str] | None = None, admin_notes: Sequence[str] | None = None
    ) -> FlatHunterExtractionV1:
        self._validate_images(images)
        prompt = build_extraction_prompt(text_inputs=text_inputs or (), admin_notes=admin_notes or ())
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images:
            encoded = base64.b64encode(image.data).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{image.mime_type};base64,{encoded}"}})
        started = time.perf_counter()
        try:
            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self.model_name,
                messages=[{"role": "user", "content": content}],
                temperature=0.1,
                max_completion_tokens=2048,
                reasoning_effort="none",
                response_format={"type": "json_object"},
            )
            latency_ms = (time.perf_counter() - started) * 1000
            usage = getattr(response, "usage", None)
            self.last_metadata = {
                "request_id": getattr(response, "_request_id", None) or getattr(response, "request_id", None),
                "input_tokens": getattr(usage, "prompt_tokens", None),
                "output_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
            result = self._validated_result((response.choices[0].message.content or "").strip())
            self._trace(image_count=len(images), latency_ms=latency_ms, success=True, validation_success=True)
            return result
        except VisionValidationError as error:
            self._trace(image_count=len(images), latency_ms=(time.perf_counter() - started) * 1000, success=False, validation_success=False, error=str(error))
            raise
        except Exception as error:
            body = getattr(error, "body", None)
            provider_error = body.get("error", {}) if isinstance(body, dict) else {}
            code = provider_error.get("code")
            message = provider_error.get("message") or str(error)
            self.last_metadata = {"request_id": getattr(error, "request_id", None), "provider_error_code": code}
            typed_error: VisionProviderError
            if code == "json_validate_failed":
                typed_error = VisionJSONModeError(
                    f"Groq vision request reached JSON generation but server-side validation failed: {message}",
                    provider=self.provider_name,
                    code=code,
                )
            else:
                typed_error = VisionProviderError(message, provider=self.provider_name, code=code)
            self._trace(image_count=len(images), latency_ms=(time.perf_counter() - started) * 1000, success=False, validation_success=False, error=str(typed_error))
            raise typed_error from error


class GeminiVisionProvider(_BaseVisionProvider):
    provider_name = "gemini"

    def __init__(self, *, api_key: str, model_name: str = "gemini-2.5-flash-lite", model: Any = None) -> None:
        super().__init__(model_name=model_name)
        if not api_key.strip():
            raise ValueError("GEMINI_API_KEY is required when VISION_PROVIDER=gemini.")
        if model is None:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(model_name)
        self.model = model

    async def extract_listing(
        self, *, images: Sequence[VisionImage], text_inputs: Sequence[str] | None = None, admin_notes: Sequence[str] | None = None
    ) -> FlatHunterExtractionV1:
        self._validate_images(images)
        prompt = build_extraction_prompt(text_inputs=text_inputs or (), admin_notes=admin_notes or ())
        request_parts: list[Any] = [prompt]
        request_parts.extend({"mime_type": image.mime_type, "data": image.data} for image in images)
        started = time.perf_counter()
        try:
            import google.generativeai as genai
            response = await asyncio.to_thread(
                self.model.generate_content,
                request_parts,
                generation_config=genai.GenerationConfig(response_mime_type="application/json", temperature=0.1),
            )
            latency_ms = (time.perf_counter() - started) * 1000
            usage = getattr(response, "usage_metadata", None)
            self.last_metadata = {
                "request_id": getattr(response, "response_id", None),
                "input_tokens": getattr(usage, "prompt_token_count", None),
                "output_tokens": getattr(usage, "candidates_token_count", None),
                "total_tokens": getattr(usage, "total_token_count", None),
            }
            result = self._validated_result(response.text.strip())
            self._trace(image_count=len(images), latency_ms=latency_ms, success=True, validation_success=True)
            return result
        except VisionValidationError as error:
            self._trace(image_count=len(images), latency_ms=(time.perf_counter() - started) * 1000, success=False, validation_success=False, error=str(error))
            raise
        except Exception as error:
            typed_error = VisionProviderError(str(error), provider=self.provider_name)
            self._trace(image_count=len(images), latency_ms=(time.perf_counter() - started) * 1000, success=False, validation_success=False, error=str(error))
            raise typed_error from error
