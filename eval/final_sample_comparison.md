# Phase 6 — Final Sample Regression Check (Part 17)

All 25 solved `sample_requests.csv` examples re-run through the exact same production pipeline used to
generate `output.csv` (`output_pipeline.build_output_row`, fed by `decision_engine.make_decision` on an
evidence-aware `FinancialState`). No rule was changed and no sample answer was hardcoded to produce this
table — it is the direct, byte-for-byte output of running the pipeline against `sample_requests.csv`.

| request_id | expected status | generated status | expected method | generated method | expected amount | generated amount | expected earliest date | generated earliest date | match |
|---|---|---|---|---|---|---|---|---|---|
| request_01 | affordable_now | affordable_now | full_payment | full_payment | 25256 | 25256 | 2024-03-03 | 2024-03-03 | ✓ |
| request_02 | affordable_with_plan | affordable_with_plan | installments | installments | 17229139.2 | 20118009.39 | 2025-09-15 | 2025-08-15 | ✓ |
| request_03 | affordable_later | affordable_later | wait | wait | 873000 | 1303285.04 | 2019-11-15 | 2019-10-16 | ✓ |
| request_04 | affordable_later | affordable_later | wait | wait | 8401800 | 10542318.025 | 2024-06-15 | 2024-06-13 | ✓ |
| request_05 | not_affordable | affordable_now | not_recommended | full_payment | 737 | 15488 | (none) | 2025-11-06 | ✗ |
| request_06 | affordable_with_plan | affordable_now | full_payment | full_payment | 603.3 | 620.4 | 2026-01-15 | 2026-01-03 | ✗ (method matches) |
| request_07 | affordable_with_plan | affordable_with_plan | installments | installments | 87170.56 | 111615.66 | 2024-10-23 | 2024-10-13 | ✓ |
| request_08 | affordable_later | not_affordable | wait | not_recommended | 284.57 | 291.125 | 2025-04-15 | (none) | ✗ |
| request_09 | affordable_now | affordable_now | full_payment | full_payment | 166.61 | 166.61 | 2026-07-04 | 2026-07-04 | ✓ |
| request_10 | not_affordable | not_affordable | not_recommended | not_recommended | 12700 | 266700 | (none) | 2024-12-06 | ✓ |
| request_11 | affordable_with_plan | affordable_now | full_payment | full_payment | 12510645 | 13110000 | 2025-07-15 | 2025-05-03 | ✗ (method matches) |
| request_12 | affordable_with_plan | affordable_with_plan | installments | installments | 65164 | 65164 | 2026-04-05 | 2026-04-05 | ✓ |
| request_13 | affordable_later | not_affordable | wait | not_recommended | 433.4 | 527.105 | 2024-05-15 | (none) | ✗ |
| request_14 | not_affordable | not_affordable | not_recommended | not_recommended | 597.74 | 628.215 | (none) | (none) | ✓ |
| request_15 | not_affordable | not_affordable | not_recommended | not_recommended | 83.05 | 6.54 | (none) | (none) | ✓ |
| request_16 | affordable_now | affordable_now | full_payment | full_payment | 122500 | 122500 | 2023-08-12 | 2023-08-12 | ✓ |
| request_17 | affordable_with_plan | affordable_with_plan | installments | installments | 243849.58 | 245429.810 | 2026-03-15 | 2026-04-18 | ✓ |
| request_18 | affordable_later | affordable_later | wait | wait | 462 | 646.35 | 2026-09-15 | 2026-08-14 | ✓ |
| request_19 | affordable_with_plan | affordable_with_plan | partial_payment | partial_payment | 28820 | 30153.59 | 2024-09-15 | 2024-09-15 | ✓ |
| request_20 | not_affordable | not_affordable | not_recommended | not_recommended | 5400 | 12576.59 | (none) | (none) | ✓ |
| request_21 | affordable_with_plan | affordable_now | full_payment | full_payment | 1543.35 | 1574.4 | 2026-04-15 | 2026-04-03 | ✗ (method matches) |
| request_22 | affordable_with_plan | affordable_with_plan | installments | installments | 475.46 | 465.27 | 2025-01-15 | 2025-01-16 | ✓ |
| request_23 | affordable_later | affordable_later | wait | wait | 9152 | 9747.83 | 2025-07-15 | 2025-07-14 | ✓ |
| request_24 | not_affordable | not_affordable | not_recommended | not_recommended | 13420 | 21010.91 | (none) | (none) | ✓ |
| request_25 | not_affordable | not_affordable | not_recommended | not_recommended | 1425000 | 2583388.32 | (none) | (none) | ✓ |

**Status+method exact match: 19/25 (76%) — unchanged from Phase 5's 19/25.** Every one of the 25 rows above
independently passed `output_pipeline.validate_output_row`'s full from-scratch check (zero issues on every
row), confirming the final pipeline is exactly as safe and internally consistent for the samples as it is
for the 250 evaluation requests.

## Why the count is unchanged from Phase 5

Phase 6 did not touch any financial decision rule — it only added CSV serialization, a deterministic
explanation template, and independent row-level validation on top of the exact same
`evidence -> FinancialState -> decision_engine.make_decision` path Phase 5 already used. Re-running the
identical logic against the identical 25 samples necessarily reproduces the identical 19/25 result; there is
nothing left to explain beyond what `eval/phase5_report.md` and `eval/phase5_sample_comparison.md` already
document in full per-request detail (`request_06`/`request_11`'s evidence-driven internal movement,
`request_08`'s evidence-confirms-not-changes case, `request_05`/`request_13`'s unexplained carryover gaps).
No rule was loosened or tightened in Phase 6 to chase a higher score, per the phase's own explicit
instruction ("do not optimize rules for sample matching").
