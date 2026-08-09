# Groq Vision Manual Smoke Test

This is an isolated experiment to determine whether Groq's `qwen/qwen3.6-27b`
model can understand FlatHunter rental-listing screenshots. It does **not**
change FlatHunter's Telegram bot, ingestion, LLM provider, matching, database,
configuration, prompts, or dependencies.

The harness uses the repository's existing `app.config.settings.groq_api_key`
and therefore the existing `.env.local` configuration convention. It never
prints the API key.

## Requirements

`GROQ_API_KEY` must be set in the existing FlatHunter configuration. The
repository already declares the required `groq` Python package. Run commands
from the repository root, preferably using its virtual environment.

## Run a single-image test

Place a real `.jpg`, `.jpeg`, `.png`, or `.webp` rental screenshot under
`samples/` (or supply another local path), then run:

```bash
.venv\\Scripts\\python.exe tests_manual/groq_vision/test_single_image.py \
  tests_manual/groq_vision/samples/property_001.jpg
```

## Run a multi-image test

Use one to five screenshots which all describe **one** property:

```bash
.venv\\Scripts\\python.exe tests_manual/groq_vision/test_multiple_images.py \
  tests_manual/groq_vision/samples/property_001_a.jpg \
  tests_manual/groq_vision/samples/property_001_b.jpg
```

Each run makes one API request and writes a timestamped JSON evidence file to
`results/`. It includes the model response, input filenames, success status,
latency, token usage when returned by Groq, and request ID when available.

## Manual evaluation

Test approximately 5–10 real Hyderabad rental screenshots. For each result,
compare it against the screenshot and check:

- rent
- location/locality
- deposit
- listing type
- contact number
- available date
- hallucinated fields
- unknown fields remaining `null`

For multi-image tests, also check that the images are combined as one property
and that contradictory facts appear in `conflicts`.

## Success criteria

Groq is suitable for further FlatHunter evaluation only if the configured
account can call `qwen/qwen3.6-27b`; it accepts local screenshots; consistently
returns valid structured JSON; correctly reads visible rent, locality, contact
details, and private-room versus entire-flat context; leaves missing fields
`null`; avoids recurring hallucinations; supports one-property multi-image
input; and has adequate free-tier/rate-limit behavior for MVP testing.

These scripts only produce evidence. They do not recommend or perform a
production provider migration.

## Common outcomes

- `Missing GROQ_API_KEY`: add it to the existing `.env.local` configuration.
- `Invalid API key`: replace or correct the configured key.
- `Model unavailable` or `Model access denied`: the model is unavailable to the
  current account or vision access is not enabled.
- `Rate limited / HTTP 429`: wait for the rate-limit window or use an account
  with adequate testing capacity.
- `Image too large`: reduce the local image below 20 MB.
- `Unsupported image format`: use `.jpg`, `.jpeg`, `.png`, or `.webp`.
- `Invalid JSON response`: the API worked, but the model did not meet the
  structured-output requirement for that input.
- `Network failure`: retry after confirming Internet access.
