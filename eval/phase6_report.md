# Phase 6 Report — Final Production Run + Output Validation + Usage Report + Submission Package

**Scope (per `implementation/phase6.md`):** the final production phase. Wires every earlier phase's module
into ONE authoritative pipeline (`code/output_pipeline.py`), generates `output.csv` at the repository root
for all 250 evaluation requests, independently validates every row from scratch, produces
`evaluation/usage_report.md` and `eval/final_sample_comparison.md`, and packages the solution as `code.zip`.
No financial decision rule was changed in this phase — only serialization, explanation generation, and
validation were added on top of Phases 1-5's already-tested logic.

---

## 1. Final Pipeline Summary

```
DataStore -> FinancialState -> EvidenceBundle -> Evidence-aware State -> Forecast
   -> Candidate Payment Plans -> Spending-change Candidates -> Verifier -> Decision Engine -> Output Row
```

Implemented in `code/output_pipeline.py`, reusing every module built in Phases 1-5 unchanged:
`data_loader`, `currency`, `financial_state`, `evidence`, `forecast`, `payment_plans`,
`spending_changes`, `verifier`, `decision_engine`. New in this phase: `format_money`/`format_payment_plan`/
`format_spending_changes` (CSV serialization), `build_decision_explanation` (deterministic template),
`build_output_row`, `validate_output_row` (independent, from-scratch, 24-point re-check of the final row),
and `run_production_pipeline`/`write_output_csv`/`read_output_csv` (orchestration). `code/main.py`'s new
`_run_phase6_production` calls this same pipeline to generate `output.csv` — there is exactly ONE
production decision path, used both by `main.py` and by this report's analysis scripts.

No special-casing by `request_id` exists anywhere in the pipeline; no sample answer is hardcoded.

## 2. Test Suite Result

```bash
python -m unittest discover -s code/tests -p "test_*.py" -v
```

**Result: 291 / 291 passed, 0 failed, 0 errors** — all 264 Phase 1-5 tests (unchanged) plus 27 new
`code/tests/test_output_pipeline.py` tests (serialization round-trips, the explanation template's forbidden-
claim guards, tampered-row detection for every validation category, and full end-to-end runs against the
real 250-request dataset, including a determinism check across two independent runs).

## 3. 250-Request Result

All 250 requests in `dataset/requests.csv` were processed with **0 exceptions, 0 crashes, 0 failed
requests** — 250 successful rows written to `output.csv`.

## 4. Final Status Counts

```
affordable_now:       73
affordable_later:     33
affordable_with_plan: 67
not_affordable:       77
```

## 5. Final Payment-Method Counts

```
full_payment:    78
wait:            33
installments:    54
partial_payment:  8
not_recommended: 77
```

## 6. Spending-Change Count

**5 of 250 requests** recommend a spending change (`request_50`, `request_77`, `request_132`,
`request_136`, `request_204` — see `eval/phase5_report.md` for the exact operations). Every one uses
exactly 1 of the permitted 3 changes; none violates a profile policy (independently re-verified per row).

## 7. Evidence Statistics

- **148 total evidence facts** (15 image + 133 message), **3 warnings** (1 genuinely unresolvable image
  amount, 2 scam/prompt-injection detections producing 0 facts) — unchanged from Phase 5, since Phase 6 did
  not touch evidence extraction.
- **109 of 250 requests'** users had at least one evidence-driven state change applied.
- **55 of 250 requests** had their FINAL decision signature (status, method, amount, earliest date, or
  spending changes) change once evidence was applied, versus the no-evidence baseline — a broader,
  field-level materiality signal than Phase 5's status/method-only count of 33 (which is a subset of these
  55; the extra 22 are cases where evidence shifted `amount_safe_to_pay` or the projected date without
  flipping the overall status/method). This is an intentional refinement introduced by this phase's
  materiality check in `run_production_pipeline`, not an unexplained discrepancy from Phase 5's diagnostics
  — the status/method-only figure (33) and the underlying per-request evidence resolutions are byte-for-byte
  identical to Phase 5's.
- **0 unresolved-evidence-in-horizon warnings** on the final 250-request run (Phase 4's raw baseline,
  without evidence, had 2; resolving 15 of 16 blank amounts via image evidence closed both of those cases).

## 8. Final `output.csv` Row Count

**251 lines: 1 header row + exactly 250 data rows.** Header matches the required schema exactly:
`request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation`.
Every `request_id` in `dataset/requests.csv` appears exactly once; no duplicates, no missing IDs, no extra
IDs (verified by `code/tests/test_output_pipeline.py`'s `test_write_and_read_back_output_csv` and re-verified
live in `code/main.py`'s Phase 6 read-back section on every run).

## 9. Output Schema Validation Result

**0 schema violations.** Every row's `affordability_status`/`recommended_payment_method` is one of the
allowed values, and every status/method combination is one of the four allowed pairings
(`affordable_now`+`full_payment`, `affordable_with_plan`+{`full_payment`,`partial_payment`,`installments`},
`affordable_later`+`wait`, `not_affordable`+`not_recommended`).

## 10. Independent Safety-Audit Result

`validate_output_row` re-derives and re-checks all 24 Part 8 rules against the FINAL, already-serialized
row — re-parsing the actual CSV strings (never trusting the in-memory `PaymentPlan` alone) and re-running
`forecast.simulate_payments`/`forecast.can_safely_pay` fresh, never reusing a stored `VerificationResult`.
Across all 250 rows:

```
schema violations          = 0
numeric violations         = 0
payment-plan violations    = 0
deadline violations        = 0
unsafe-plan violations     = 0
spending-change violations = 0
duplicate/missing request IDs = 0
```

Additional spot-checks performed live against the real generated `output.csv` (Part 18):
- **0** spending-change entries reference a synthetic/unknown `event_id` — every one of the 5 real cases
  targets a genuine `financial_events.csv` row.
- Every request with `amount_safe_to_pay == 0` (7 requests) is paired with `not_affordable/not_recommended`
  — a legitimate "zero safe headroom today" fact, never a blank-amount-event silently defaulting to zero
  (the 16 real blank-amount events are a structurally different concept, handled entirely inside
  `evidence.py`'s image resolution, and are separately confirmed never to become zero by
  `test_evidence.py`'s `test_image_resolution_never_produces_a_zero_amount`).
- Protected/inflexible-category and historical-event-immutability guarantees are enforced by
  `spending_changes.validate_spending_change`/`apply_spending_changes` (re-checked independently by every
  row's validation) and by the extensive Phase 1-5 test suite (204 tests) that exercises them directly;
  Phase 6 did not modify any of this logic.

## 11. 25-Sample Final Comparison

**Status+method exact match: 19/25 (76%) — unchanged from Phase 5's 19/25.** Full table and per-request
reasoning: `eval/final_sample_comparison.md`. Every one of the 25 rows independently passed
`validate_output_row` with zero issues. No rule was changed to chase a higher match count in this phase.

## 12. Runtime

Full 250-request run (measured live inside `code/main.py`'s Phase 6 section, and reproduced identically from
a standalone extracted `code.zip`):

```
total:      4.39s   (~17.5 ms/request)
state construction:  0.48s  (~1.9 ms/request)
evidence processing: 0.04s  (~0.2 ms/request)
decision (incl. a second pass for evidence-materiality comparison where applicable): 1.05s (~4.2 ms/request)
independent row validation (re-simulates every recommended plan from scratch): 1.06s (~4.2 ms/request)
```

Fully local; no network I/O anywhere in the pipeline.

## 13. Usage/Cost Statistics

**0 model/API calls, 0 tokens, $0.00 estimated cost.** Full detail, including why (offline image cache +
deterministic regex message extraction, no LLM anywhere in the decision or explanation path):
`evaluation/usage_report.md`.

## 14. Reproducibility Result

**Confirmed byte-identical.** `python code/main.py` was run twice in sequence from the same dataset; the two
resulting `output.csv` files are byte-for-byte identical (`diff` reports no differences). The same check is
codified as `test_output_pipeline.py`'s `test_pipeline_is_deterministic_across_two_runs`. Additionally, the
packaged `code.zip` was extracted to a clean standalone directory (with only a copy of `dataset/` placed
alongside it, no other repo files) and run independently; its `output.csv` is byte-for-byte identical to the
one produced by the working tree, confirming the pipeline has no hidden dependency on repository state,
absolute paths, or execution order.

## 15. `code.zip` Contents

33 files, `zipfile.ZipFile.testzip()` reports no corruption. Contents:

```
AGENTS.md, README.md
code/{currency,data_loader,decision_engine,evidence,financial_state,forecast,
      main,output_pipeline,payment_plans,spending_changes,verifier}.py
code/evidence_cache/image_extractions.json
code/tests/test_{currency,data_loader,decision_engine,evidence,financial_state,
                  forecast,output_pipeline,payment_plans,spending_changes,verifier}.py
eval/{phase0_reconnaissance,phase1_report,phase2_report,phase3_report,phase4_report,
      phase5_report,phase5_sample_comparison,final_sample_comparison}.md
evaluation/usage_report.md
```

**Confirmed absent:** `.venv`, `__pycache__`, `*.pyc`, `dataset/` (organizer-provided, not a project
deliverable), `log.txt` (internal session log, explicitly git-ignored per `AGENTS.md` §2), API keys/secrets
(none exist anywhere in this codebase — verified by inspection; there is nothing to redact). Two empty,
unused stub files inherited from the original starter scaffold (`code/evaluation/main.py`,
`code/evaluation/usage_report.md` — both 0 bytes, superseded by the real `code/main.py` and the real
top-level `evaluation/usage_report.md`) were deliberately excluded from the package to avoid a confusing
duplicate `evaluation/` path; they were left untouched in the working tree itself rather than deleted, since
removing tracked starter-repo files was outside this phase's scope.

## 16. Dataset Immutability Result

`git status --porcelain dataset/` is empty both before and after this phase's entire run (verified
explicitly). The pipeline only ever reads `dataset/` via `code/data_loader.DataStore.load()`; no code path in
this project opens any file under `dataset/` for writing. `dataset/output.csv` (the blank prediction
template) is untouched — the real `output.csv` is written to the repository root, as required (Part 2).

## 17. Remaining Known Discrepancies

Unchanged from Phase 5, since no financial-decision rule changed in Phase 6: the same recurring-amount
estimation gap documented since Phase 3 explains most numeric (non-status) differences against the 25
samples, and `request_05`/`request_13` remain honestly-flagged, unexplained gaps with no message, image, or
unresolved event traced to them. Full detail: `eval/phase5_report.md`, `eval/final_sample_comparison.md`.

## 18. Confirmation: No Sample Answers Were Hardcoded

`code/output_pipeline.py` and every module it calls take only `request_id`-agnostic inputs (a `Request`
object's own fields, a `FinancialState`, an `EvidenceBundle`, supplied `PaymentOption`s) and contain no
conditional branch, lookup table, or special case keyed on any specific `request_id`, `user_id`, or sample
answer anywhere in the codebase (verified by inspection and by the fact that `sample_requests.csv`'s 25
users are structurally disjoint from `requests.csv`'s 250 evaluation users — the pipeline that decides the
250 evaluation rows shares code with, but no data from, the samples). The 19/25 sample match rate is a
measurement of the pipeline's accuracy, never an input to it.

## 19. Confirmation: Phase 6 Is Complete

All Part 28 final-acceptance criteria are satisfied (test suite passes; `output.csv` exists at the
repository root with exactly 250 data rows and the exact required header; every `request_id` unique and
valid; every `amount_safe_to_pay` within `[0, requested_amount]`; every payment plan internally valid,
independently verified safe, and within `desired_completion_date`; installment plans match supplied options;
partial payments have exactly two payments; spending changes are valid, ≤3, and touch no protected/
inflexible expense; no unresolved blank amount became zero; evidence remains bounded; `dataset/` is
unchanged; `evaluation/usage_report.md` describes this actual final run; `code.zip` exists, contains the
required code and no secrets/`.venv`/`__pycache__`; the pipeline is reproducible;
`eval/final_sample_comparison.md` and this report both exist; `log.txt` requirements from `AGENTS.md` are
satisfied). **Phase 6 is complete. Per its own explicit instruction, no further development phase was
started.**
