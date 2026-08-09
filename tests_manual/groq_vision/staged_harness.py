"""Shared implementation for the staged, isolated Groq Vision experiments."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from harness import HarnessError, MODEL, RESULTS_DIRECTORY, image_content, load_api_key, validate_images


def _error_details(exc: Exception) -> dict[str, Any]:
    body = getattr(exc, "body", None)
    error = body.get("error", {}) if isinstance(body, dict) else {}
    return {
        "error_message": error.get("message") or str(exc).strip() or exc.__class__.__name__,
        "error_code": error.get("code"),
        "failed_generation": error.get("failed_generation"),
        "request_id": getattr(exc, "request_id", None) or getattr(exc, "_request_id", None),
        "status_code": getattr(exc, "status_code", None),
    }


def _classify_error(details: dict[str, Any]) -> str:
    status = details["status_code"]
    message = details["error_message"].lower()
    if details["error_code"] == "json_validate_failed":
        return "JSON generation failed"
    if status == 401 or "invalid api key" in message or "authentication" in message:
        return "Invalid API key"
    if status == 403 or "permission" in message or "access denied" in message:
        return "Model access denied"
    if status == 404 or ("model" in message and "not found" in message):
        return "Model unavailable"
    if status == 429 or "rate limit" in message:
        return "Rate limited / HTTP 429"
    if status == 413 or "too large" in message:
        return "Image too large"
    if status == 400 and ("image" in message or "vision" in message):
        return "Image input rejected"
    if "connection" in message or "network" in message or "timeout" in message:
        return "Network failure"
    return "Groq SDK/API error"


def _save_result(result: dict[str, Any]) -> Path:
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    destination = RESULTS_DIRECTORY / f"groq_vision_{result['test_type']}_{timestamp}.json"
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return destination


def _strip_outer_code_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```") or not text.endswith("```"):
        return text
    first_newline = text.find("\n")
    return text if first_newline == -1 else text[first_newline + 1 : -3].strip()


def run_vision_test(
    image_paths: Sequence[str | Path], *, prompt: str, test_type: str, use_response_format: bool, parse_json: bool
) -> tuple[dict[str, Any], Path]:
    """Run one test stage without conflating vision access and JSON formatting."""
    result: dict[str, Any] = {
        "provider": "groq", "model": MODEL, "test_type": test_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input_files": [str(Path(path)) for path in image_paths],
        "success": False, "api_call_worked": False, "vision_tested": False,
        "json_mode_failed": False,
    }
    try:
        from groq import Groq

        images = validate_images(image_paths)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content.extend(image_content(image) for image in images)
        request: dict[str, Any] = {
            "model": MODEL,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
            "max_completion_tokens": 2048,
            "reasoning_effort": "none",
        }
        if use_response_format:
            request["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        response = Groq(api_key=load_api_key()).chat.completions.create(**request)
        result.update({
            "api_call_worked": True,
            "vision_tested": True,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "request_id": getattr(response, "_request_id", None) or getattr(response, "request_id", None),
        })
        usage = getattr(response, "usage", None)
        result.update({
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        })
        raw_text = (response.choices[0].message.content or "").strip()
        result["response_text"] = raw_text
        if not parse_json:
            result["success"] = bool(raw_text)
            if not raw_text:
                result.update({"error_category": "Empty model response", "error": "Groq returned no visible plaintext."})
        else:
            candidate = raw_text if use_response_format else _strip_outer_code_fence(raw_text)
            try:
                result["response"] = json.loads(candidate)
            except json.JSONDecodeError as exc:
                result.update({"error_category": "Invalid JSON response", "error": f"Response was not valid JSON: {exc.msg}"})
            else:
                result["success"] = isinstance(result["response"], dict)
                if not result["success"]:
                    result.update({"error_category": "Invalid JSON response", "error": "Response parsed but was not a JSON object."})
    except HarnessError as exc:
        result.update({"error_category": exc.category, "error": exc.message})
    except Exception as exc:
        details = _error_details(exc)
        result.update(details)
        result.update({"error_category": _classify_error(details), "error": details["error_message"]})
        if details["error_code"] == "json_validate_failed":
            result["json_mode_failed"] = True
    return result, _save_result(result)


def print_outcome(result: dict[str, Any], destination: Path) -> int:
    if result["success"]:
        print("TEST PASSED")
        print(json.dumps(result["response"], indent=2, ensure_ascii=False) if "response" in result else result["response_text"])
        print(f"Result saved: {destination}")
        return 0
    print("VISION API MAY WORK, JSON GENERATION FAILED" if result["json_mode_failed"] else "TEST FAILED")
    print(f"Reason: {result.get('error_category', 'Unknown error')}")
    print(result.get("error", "No error details returned."))
    print(f"Result saved: {destination}")
    return 1
