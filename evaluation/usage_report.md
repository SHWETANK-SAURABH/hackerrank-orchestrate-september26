# Usage Report — Final Full-Dataset Run

This report describes the exact run of `python code/main.py` that produced the submitted `output.csv`
(repository root), via the Phase 6 production pipeline (`code/output_pipeline.py`).

## Run Details

| Item | Value |
|---|---|
| 1. Execution date/time | 2026-09-13T00:18+05:30 |
| 2. Pipeline version/phase | Phase 6 (final production pipeline: `DataStore -> FinancialState -> EvidenceBundle -> evidence-aware State -> Forecast -> Candidate Payment Plans -> Spending-change Candidates -> Verifier -> Decision Engine -> Output Row`) |
| 3. Requests processed | 250 (every `request_id` in `dataset/requests.csv`) |
| 4. Successful requests | 250 |
| 5. Failed requests | 0 |
| 6. Providers/models used | **none** |
| 7. Number of model calls | **0** |
| 8. Input tokens | **0** |
| 9. Output tokens | **0** |
| 10. Total tokens | **0** |
| 11. Average tokens/request | **0** |
| 12. Estimated cost | **$0.00** |
| 13. Average cost/request | **$0.00** |
| 14. Image-evidence calls (live) | **0** |
| 15. Message-evidence calls (live) | **0** |
| 16. External APIs used | **No** |
| 17. Runtime (this run) | total 4.39s for 250 requests (~17.5 ms/request); breakdown below |
| 18. Caching behavior | Image evidence uses a pre-computed, offline JSON cache (`code/evidence_cache/image_extractions.json`, 16 entries, one per dataset image) built once from a human/vision-capable review of the real images — looked up by `image_id` at runtime, no network access, no API key. |
| 19. Fallback behavior | An image with no cached entry, or a cached entry with `amount: null` (one real case — `event_1700`/`image_04`, a cropped receipt whose total is not legible), is left **unresolved** — the blank amount is never guessed at or defaulted to zero. A message matching none of the deterministic extraction rules produces no fact at all, and is silently skipped, never invented. |
| 20. Errors/warnings | 3 evidence-bundle warnings across the whole dataset: 1 genuinely unresolvable image amount (`event_1700`), and 2 scam/prompt-injection-shaped messages (`message_142`, `message_67`) correctly detected and rejected before producing any fact. 0 crashes, 0 exceptions, 0 rows failing independent validation. |

## Why 0 Model Calls

This implementation never calls a live LLM, vision model, or any external API at runtime, for any request:

- **Image evidence** — all 16 real dataset images were read once, by a multimodal-capable review during
  development, and the structured result (`{amount, currency, confidence, evidence_text}`) was cached to
  `code/evidence_cache/image_extractions.json`. `CachedVisionExtractor` (in `code/evidence.py`) does a pure
  dictionary lookup by `image_id` at runtime — no network call, no API key, fully deterministic and
  reproducible on any machine with no credentials configured.
- **Message evidence** — `code/evidence.py`'s `extract_message_facts` uses a fixed table of ~26 bilingual
  (English/Bahasa Indonesia) regular expressions matched against `messages.csv`'s finite set of real message
  templates. No model of any kind is invoked.
- **Decision logic** — every affordability/payment-plan/spending-change decision is pure deterministic
  Python (`Decimal` arithmetic, date arithmetic, and rule-based branching) in `code/financial_state.py`,
  `code/forecast.py`, `code/payment_plans.py`, `code/spending_changes.py`, `code/verifier.py`, and
  `code/decision_engine.py`. Nothing here is model-generated.
- **`decision_explanation`** — built by `code/output_pipeline.py`'s `build_decision_explanation`, a fixed
  Python string template over the decision's own already-verified fields. No LLM is used to write or
  paraphrase explanations, so no explanation can contradict the actual plan (per the challenge's own
  requirement).

No API keys, credentials, or provider configuration exist anywhere in this codebase, and none are required
to reproduce this run.

## Runtime Breakdown (this run, 250 requests)

| Stage | Seconds | ms/request |
|---|---|---|
| FinancialState construction | 0.478 | 1.91 |
| Evidence processing (`apply_evidence`) | 0.039 | 0.16 |
| Decision (`make_decision`, incl. forecast + candidate generation + spending-change search + verification, run twice per request when evidence is present to detect materiality) | 1.050 | 4.20 |
| Independent row validation (`validate_output_row`, re-simulating every recommended plan from scratch) | 1.062 | 4.25 |
| **Total (incl. evidence-bundle build, CSV write, read-back)** | **4.39** | **17.5** |

Measured via `python code/main.py`'s Phase 6 diagnostic section, which is the same code path used to
generate the submitted `output.csv` (single authoritative production path — see `eval/phase6_report.md`).
Re-running the pipeline twice from the same dataset produces byte-identical `output.csv` files (verified),
confirming the reported runtime reflects a fully deterministic process with no variance from model latency,
retries, or non-determinism of any kind.
