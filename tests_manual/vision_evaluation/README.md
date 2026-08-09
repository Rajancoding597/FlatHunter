# FlatHunter Vision Golden Evaluation

This harness calls the explicitly selected vision provider and compares its
validated `FlatHunterExtractionV1` output with small deterministic golden
fixtures. It never writes evaluation data to Supabase.

Run Case #001 with Groq:

```powershell
python tests_manual/vision_evaluation/run_case.py case_001 --provider groq
```

Run every case with an explicit provider:

```powershell
python tests_manual/vision_evaluation/run_all.py --provider groq
python tests_manual/vision_evaluation/run_all.py --provider gemini
```

Reports compare critical canonical values, null/unknown handling, contacts,
additional cost/context preservation, and canonical fields that may have been
hallucinated. Actual responses are saved beside each case for manual review.
