"""Run one FlatHunter vision golden case without writing to Supabase."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.vision.factory import get_vision_provider
from app.vision.providers import VisionImage

CASES = Path(__file__).resolve().parent / "cases"


def compare(expected: dict, actual: dict) -> dict:
    mismatches = []
    hallucinated = []
    actual_canonical = actual.get("canonical", {})
    for field, expected_value in expected["canonical"].items():
        actual_value = actual_canonical.get(field)
        if actual_value != expected_value:
            mismatches.append({"field": field, "expected": expected_value, "actual": actual_value})
        if expected_value is None and actual_value is not None:
            hallucinated.append(field)
    if actual.get("content_type") != expected["content_type"]:
        mismatches.append({"field": "content_type", "expected": expected["content_type"], "actual": actual.get("content_type")})
    if actual.get("contacts") != expected["contacts"]:
        mismatches.append({"field": "contacts", "expected": expected["contacts"], "actual": actual.get("contacts")})
        if expected["contacts"] == [] and actual.get("contacts"):
            hallucinated.append("contacts")
    context_text = json.dumps(actual.get("additional_attributes", {}), ensure_ascii=False).lower()
    missing_context = [str(value) for value in expected["required_context_values"] if str(value) not in context_text]
    missing_context.extend(term for term in expected["required_context_terms"] if term not in context_text)
    return {
        "passed": not mismatches and not missing_context,
        "critical_mismatches": mismatches,
        "hallucinated_canonical_facts": hallucinated,
        "missing_additional_context": missing_context,
    }


async def run(case_name: str, provider_name: str) -> int:
    case_dir = CASES / case_name
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    configured = settings.model_copy(update={"vision_provider": provider_name})
    provider = get_vision_provider(configured)
    images = []
    for relative in case["inputs"]:
        path = (case_dir / relative).resolve()
        mime = "image/png" if path.suffix.lower() == ".png" else "image/webp" if path.suffix.lower() == ".webp" else "image/jpeg"
        images.append(VisionImage(data=path.read_bytes(), mime_type=mime, source_id=path.name))
    extracted = await provider.extract_listing(images=images)
    actual = extracted.model_dump(mode="json")
    evaluation = compare(expected, actual)
    output = {"case": case_name, "provider": provider_name, "model": provider.model_name, "actual": actual, "evaluation": evaluation, "model_metadata": provider.last_metadata}
    destination = case_dir / f"actual_{provider_name}.json"
    destination.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"Saved: {destination}")
    return 0 if evaluation["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", help="Case directory name, for example case_001")
    parser.add_argument("--provider", choices=["groq", "gemini"], default=settings.vision_provider)
    arguments = parser.parse_args()
    return asyncio.run(run(arguments.case, arguments.provider))


if __name__ == "__main__":
    raise SystemExit(main())
