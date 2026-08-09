"""Prompts used by the isolated Groq vision smoke tests."""

SINGLE_IMAGE_PROMPT = """You are performing a vision capability smoke test for a rental-property application.

Analyze the attached image carefully.

Your job is NOT to guess.

Only report information that is visibly present or explicitly written in the image.

Return ONLY valid JSON using exactly this structure:

{
  "vision_working": true,
  "image_type": "rental_listing | chat_screenshot | property_photo | other",
  "visible_text_summary": "",
  "rental_information": {
    "location": null,
    "rent": null,
    "deposit": null,
    "property_type": null,
    "available_from": null,
    "contact_number": null
  },
  "unreadable_or_uncertain": [],
  "confidence": 0.0
}

Rules:

1. Never invent information.
2. If a value is not visible, return null.
3. Unknown does not mean false.
4. If text is unclear, add the relevant field to unreadable_or_uncertain.
5. Preserve phone numbers exactly as visible.
6. Do not infer missing values.
7. Do not include Markdown.
8. Do not add commentary outside the JSON.
9. vision_working should be true only if you were actually able to inspect the supplied image.
"""

MULTIPLE_IMAGE_PROMPT = """All attached images relate to ONE rental property.

Combine supported information across all images.

Do not treat them as different properties.

If two images contain contradictory important facts, report the contradiction instead of choosing arbitrarily.

Only report information that is visibly present or explicitly written in the images. Never invent information.

Return ONLY valid JSON using exactly this structure:

{
  "vision_working": true,
  "property": {
    "location": null,
    "rent": null,
    "maintenance": null,
    "deposit": null,
    "property_type": null,
    "configuration": null,
    "available_from": null
  },
  "contacts": [],
  "conflicts": [],
  "unreadable_or_uncertain": [],
  "confidence": 0.0
}

If a value is not visible, return null. If text is unclear, add the relevant field to
unreadable_or_uncertain. Do not include Markdown or commentary outside the JSON.
"""
