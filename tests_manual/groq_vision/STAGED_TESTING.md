# Staged Groq Vision Test Commands

The experiment now separates image understanding from server-side JSON validation.
Run each stage from the repository root using the same two screenshots:

```powershell
python tests_manual/groq_vision/test_vision_plaintext_stage.py tests_manual/groq_vision/samples/6323322407233459435.jpg tests_manual/groq_vision/samples/6323322407233459436.jpg
python tests_manual/groq_vision/test_vision_simple_json.py tests_manual/groq_vision/samples/6323322407233459435.jpg tests_manual/groq_vision/samples/6323322407233459436.jpg
python tests_manual/groq_vision/test_vision_json_without_response_format.py tests_manual/groq_vision/samples/6323322407233459435.jpg tests_manual/groq_vision/samples/6323322407233459436.jpg
```

1. Plaintext establishes whether the model can inspect the screenshots.
2. Simple JSON uses Groq's `response_format={"type":"json_object"}` validation.
3. The final fallback omits `response_format` and attempts only strict client-side
   `json.loads` after removing one outer Markdown code fence, if present.

Run stage 3 only if stage 2 fails. Every result is saved in `results/` with
provider metadata, model, inputs, latency, token usage when returned, request
ID when returned, raw text, parsed JSON where applicable, and error details.

`json_validate_failed` is reported as `VISION API MAY WORK, JSON GENERATION
FAILED`; it is not reported as a failure of image understanding.
