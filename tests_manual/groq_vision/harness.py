"""Shared implementation for the manual Groq Vision experiment.

This module intentionally does not import or change FlatHunter's LLM provider.
It only reuses the repository's existing ``app.config.settings`` convention to
obtain ``GROQ_API_KEY``.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

MODEL = "qwen/qwen3.6-27b"
MAX_IMAGES = 5
MAX_IMAGE_BYTES = 20 * 1024 * 1024
SUPPORTED_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
RESULTS_DIRECTORY = Path(__file__).resolve().parent / "results"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class HarnessError(Exception):
    """A user-actionable failure raised before or during the API test."""

    message: str
    category: str = "Groq SDK/API error"

    def __str__(self) -> str:
        return self.message


def load_api_key() -> str:
    """Read the key through FlatHunter's existing Pydantic settings object."""
    if str(REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(REPOSITORY_ROOT))
    try:
        from app.config import settings
    except Exception as exc:  # Settings may fail when required application config is absent.
        raise HarnessError(
            "Could not load the repository's existing configuration. Ensure .env.local "
            "contains the required FlatHunter settings and GROQ_API_KEY.",
            "Missing GROQ_API_KEY",
        ) from exc

    api_key = settings.groq_api_key.strip()
    if not api_key:
        raise HarnessError(
            "GROQ_API_KEY is missing or empty in the existing FlatHunter configuration.",
            "Missing GROQ_API_KEY",
        )
    return api_key


def validate_images(paths: Sequence[str | Path]) -> list[Path]:
    """Validate image count, type, existence, and model file-size limits."""
    if not paths:
        raise HarnessError("Provide at least one local image path.", "Image input rejected")
    if len(paths) > MAX_IMAGES:
        raise HarnessError(
            f"Received {len(paths)} images, but {MODEL} supports at most {MAX_IMAGES} images per request.",
            "Image input rejected",
        )

    validated: list[Path] = []
    for supplied_path in paths:
        path = Path(supplied_path).expanduser()
        if not path.is_file():
            raise HarnessError(f"Image file does not exist: {path}", "Image input rejected")
        if path.suffix.lower() not in SUPPORTED_MIME_TYPES:
            allowed = ", ".join(SUPPORTED_MIME_TYPES)
            raise HarnessError(
                f"Unsupported image format for {path.name}. Supported formats: {allowed}.",
                "Unsupported image format",
            )
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise HarnessError(
                f"Image too large: {path.name} exceeds the {MAX_IMAGE_BYTES // (1024 * 1024)} MB Groq limit.",
                "Image too large",
            )
        validated.append(path.resolve())
    return validated


def image_content(path: Path) -> dict[str, Any]:
    """Create a Groq/OpenAI-compatible data URL image content item."""
    mime_type = SUPPORTED_MIME_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]
    if mime_type not in SUPPORTED_MIME_TYPES.values():
        raise HarnessError(f"Unsupported image format: {path.name}", "Unsupported image format")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
    }


def classify_api_error(exc: Exception) -> HarnessError:
    """Map Groq and transport failures to concise, actionable categories."""
    status_code = getattr(exc, "status_code", None)
    detail = str(exc).strip() or exc.__class__.__name__
    lower_detail = detail.lower()
    if status_code == 401 or "invalid api key" in lower_detail or "authentication" in lower_detail:
        return HarnessError("Groq rejected GROQ_API_KEY. Check that the configured key is valid.", "Invalid API key")
    if status_code == 403 or "permission" in lower_detail or "access denied" in lower_detail:
        return HarnessError(f"Groq denied access to {MODEL}: {detail}", "Model access denied")
    if status_code == 404 or "model" in lower_detail and "not found" in lower_detail:
        return HarnessError(f"Groq could not find or expose {MODEL}: {detail}", "Model unavailable")
    if status_code == 429 or "rate limit" in lower_detail:
        return HarnessError(f"Groq rate-limited the request: {detail}", "Rate limited / HTTP 429")
    if status_code == 413 or "too large" in lower_detail:
        return HarnessError(f"Groq rejected an image as too large: {detail}", "Image too large")
    if status_code == 400 and ("image" in lower_detail or "vision" in lower_detail):
        return HarnessError(f"Groq rejected the image input or vision request: {detail}", "Image input rejected")
    if "connection" in lower_detail or "network" in lower_detail or "timeout" in lower_detail:
        return HarnessError(f"Network failure while calling Groq: {detail}", "Network failure")
    return HarnessError(f"Groq SDK/API error: {detail}", "Groq SDK/API error")


def response_metadata(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        "request_id": getattr(response, "_request_id", None) or getattr(response, "request_id", None),
    }


def save_result(payload: dict[str, Any]) -> Path:
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    path = RESULTS_DIRECTORY / f"groq_vision_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return path


def run_test(image_paths: Sequence[str | Path], prompt: str) -> tuple[dict[str, Any], Path]:
    """Make exactly one vision request and persist success or failure evidence."""
    started_at = datetime.now(timezone.utc)
    input_files = [str(Path(item)) for item in image_paths]
    base_result: dict[str, Any] = {
        "provider": "groq",
        "model": MODEL,
        "timestamp": started_at.isoformat(),
        "input_files": input_files,
        "success": False,
    }
    try:
        validated_paths = validate_images(image_paths)
        api_key = load_api_key()
        from groq import Groq

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content.extend(image_content(path) for path in validated_paths)
        started = time.perf_counter()
        response = Groq(api_key=api_key).chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": content}],
            temperature=0.1,
            max_completion_tokens=1024,
            response_format={"type": "json_object"},
            reasoning_format="hidden",
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        raw_response = (response.choices[0].message.content or "").strip()
        try:
            parsed_response = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise HarnessError(
                f"Groq returned a response, but it was not valid JSON: {exc.msg}.",
                "Invalid JSON response",
            ) from exc
        if not isinstance(parsed_response, dict):
            raise HarnessError("Groq returned valid JSON but not a JSON object.", "Invalid JSON response")

        base_result.update(response_metadata(response))
        base_result.update({
            "success": True,
            "latency_ms": latency_ms,
            "raw_response": raw_response,
            "response": parsed_response,
        })
    except HarnessError as exc:
        base_result.update({"error_category": exc.category, "error": exc.message})
    except Exception as exc:  # Preserve the test evidence but never expose credentials.
        mapped_error = classify_api_error(exc)
        base_result.update({"error_category": mapped_error.category, "error": mapped_error.message})

    saved_path = save_result(base_result)
    return base_result, saved_path


def print_outcome(result: dict[str, Any], saved_path: Path) -> int:
    """Print a clear console outcome and return a process exit code."""
    if not result["success"]:
        print("API TEST FAILED")
        print(f"Reason: {result['error_category']}")
        print(result["error"])
        print(f"Result saved: {saved_path}")
        return 1

    response = result["response"]
    print("API WORKED BUT EXTRACTION QUALITY MAY BE BAD")
    print("The model accepted the image and returned valid JSON. Manually compare every field with the screenshot.")
    print(json.dumps(response, indent=2, ensure_ascii=False))
    print(f"Result saved: {saved_path}")
    return 0
