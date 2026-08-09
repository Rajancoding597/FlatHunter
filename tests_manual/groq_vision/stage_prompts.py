"""Exact prompts for staged isolated Groq Vision tests."""

PLAINTEXT_VISION_PROMPT = '''You are performing a vision capability test for FlatHunter.

Look carefully at ALL attached images.

All attached images describe ONE rental property.

Your goal is only to prove whether you can actually read and understand the images.

Tell me exactly what information is visibly present.

Please report, when visible:

- locality or location
- monthly rent
- maintenance
- security deposit
- brokerage
- property type
- whether this is an entire flat, private room, or shared room
- BHK/configuration
- available-from date
- furnishing
- attached bathroom
- parking
- phone number
- WhatsApp number
- email
- any other useful rental-property information

Rules:

1. Do not guess.
2. If something is not visible, say "unknown".
3. Unknown does not mean false.
4. Preserve phone/contact numbers exactly as visible.
5. If text is unreadable, explicitly say it is unreadable.
6. If two images contain conflicting values, report both values and say they conflict.
7. Do not invent amenities or property facts.
8. Plain-text output is expected for this test.
'''

SIMPLE_JSON_PROMPT = '''Analyze all attached images.

All attached images describe ONE rental property.

Return ONE valid JSON object and nothing else.

Use exactly these keys:

{
  "location": null,
  "rent": null,
  "maintenance": null,
  "deposit": null,
  "brokerage": null,
  "property_type": null,
  "configuration": null,
  "available_from": null,
  "contact_number": null,
  "other_details": [],
  "uncertain_fields": []
}

Rules:

1. Return JSON only.
2. Do not use Markdown.
3. Do not guess.
4. Use null when information is unknown.
5. Numbers should be JSON numbers when clearly visible.
6. Arrays must contain strings.
7. Unknown does not mean false.
8. Preserve contact numbers exactly as visible.
9. If text is unclear, put the relevant field name in uncertain_fields.
10. Do not output any keys other than those listed above.
'''
