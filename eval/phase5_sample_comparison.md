# Phase 5 — 25-Sample Comparison (Evidence + Spending-Change Integration)

Every sample request re-run through the full Phase 5 pipeline: `build_financial_state` →
`evidence.build_evidence_bundle` → `evidence.apply_evidence` (evidence-aware state) →
`decision_engine.make_decision` (which itself calls `spending_changes.generate_spending_change_candidates`
whenever no zero-change `full_payment` is already safe today). No rule was changed to force a match;
every number below is what the pipeline actually produces.

| request | sample status | generated status | sample method | generated method | sample amount | generated amount | match | evidence resolutions |
|---|---|---|---|---|---|---|---|---|
| request_01 | affordable_now | affordable_now | full_payment | full_payment | 25256 | 25256 | ✓ | 0 |
| request_02 | affordable_with_plan | affordable_with_plan | installments | installments | 17229139.2 | 20118009.39 | ✓ | 1 |
| request_03 | affordable_later | affordable_later | wait | wait | 873000 | 1303285.04 | ✓ | 2 |
| request_04 | affordable_later | affordable_later | wait | wait | 8401800 | 10542318.025 | ✓ | 0 |
| request_05 | not_affordable | affordable_now | not_recommended | full_payment | 737 | 15488 | ✗ | 0 |
| request_06 | affordable_with_plan | affordable_now | full_payment | full_payment | 603.3 | 620.4 | ✗ (method matches) | 1 |
| request_07 | affordable_with_plan | affordable_with_plan | installments | installments | 87170.56 | 111615.66 | ✓ | 0 |
| request_08 | affordable_later | not_affordable | wait | not_recommended | 284.57 | 291.125 | ✗ | 1 |
| request_09 | affordable_now | affordable_now | full_payment | full_payment | 166.61 | 166.61 | ✓ | 0 |
| request_10 | not_affordable | not_affordable | not_recommended | not_recommended | 12700 | 266700 | ✓ | 0 |
| request_11 | affordable_with_plan | affordable_now | full_payment | full_payment | 12510645 | 13110000 | ✗ (method matches) | 1 |
| request_12 | affordable_with_plan | affordable_with_plan | installments | installments | 65164 | 65164 | ✓ | 0 |
| request_13 | affordable_later | not_affordable | wait | not_recommended | 433.4 | 527.105 | ✗ | 0 |
| request_14 | not_affordable | not_affordable | not_recommended | not_recommended | 597.74 | 628.215 | ✓ | 1 |
| request_15 | not_affordable | not_affordable | not_recommended | not_recommended | 83.05 | 6.54 | ✓ | 1 |
| request_16 | affordable_now | affordable_now | full_payment | full_payment | 122500 | 122500 | ✓ | 2 |
| request_17 | affordable_with_plan | affordable_with_plan | installments | installments | 243849.58 | 245429.810 | ✓ | 2 |
| request_18 | affordable_later | affordable_later | wait | wait | 462 | 646.35 | ✓ | 0 |
| request_19 | affordable_with_plan | affordable_with_plan | partial_payment | partial_payment | 28820 | 30153.59 | ✓ | 0 |
| request_20 | not_affordable | not_affordable | not_recommended | not_recommended | 5400 | 12576.59 | ✓ | 2 |
| request_21 | affordable_with_plan | affordable_now | full_payment | full_payment | 1543.35 | 1574.4 | ✗ (method matches) | 0 |
| request_22 | affordable_with_plan | affordable_with_plan | installments | installments | 475.46 | 465.27 | ✓ | 0 |
| request_23 | affordable_later | affordable_later | wait | wait | 9152 | 9747.83 | ✓ | 0 |
| request_24 | not_affordable | not_affordable | not_recommended | not_recommended | 13420 | 21010.91 | ✓ | 0 |
| request_25 | not_affordable | not_affordable | not_recommended | not_recommended | 1425000 | 2583388.32 | ✓ | 0 |

**Status+method exact match: 19/25 (76%) — unchanged from Phase 4's 19/25.** None of the six Phase 4
mismatches (`request_05`, `request_06`, `request_08`, `request_11`, `request_13`, `request_21`) flipped to a
match, and no previously-matching sample regressed. This is expected, not a sign evidence integration did
nothing — see the per-request notes below; two of the four requests phase5.md names explicitly
(`request_06`, `request_11`) show real evidence-driven movement even though the final status still differs.

## Per-request notes for the four requests phase5.md requires explicit investigation of

- **`request_06`** — `message_04` ("Your temporary monthly pay is EUR 1037.52. The reduced amount continues
  for the next payroll.") is correctly extracted as `income_reduction` and overrides the recurring salary
  pattern (1441 → 1037.52 EUR/month) via `evidence.apply_evidence`. Despite that, `amount_safe_to_pay` only
  moves from the Phase 4 baseline to 620.4 — still short of matching the sample's own logic, which additionally
  applies `stop:event_476` to reach 603.3 via `affordable_with_plan`. Our model's raw (post-evidence) headroom
  is already large enough to clear `requested_amount` with **no** spending change needed at all, so
  `decision_engine`'s "don't change spending unless necessary" rule (Part 19) correctly never invokes
  `spending_changes.generate_spending_change_candidates` here — it only runs when a zero-change `full_payment`
  isn't already safe. The residual ~17-unit gap versus the sample is the same recurring-amount-estimation
  difference documented since Phase 3, not a spending-change defect.
- **`request_11`** — `message_08` ("Gaji pokok yang dikonfirmasi adalah IDR 38760000. Komisi ... belum
  disetujui.") is correctly extracted as `income_confirmation` and used to **anchor** a brand-new recurring
  income pattern, since Phase 2's cadence detector could never cleanly separate a stable base salary from a
  highly variable commission stream sharing `category=salary`. This is the single largest evidence-driven
  change in the whole dataset: the decision goes from Phase 4's `not_affordable/not_recommended` (amount 0) to
  `affordable_now/full_payment` (amount 13,110,000 — the full requested amount). The sample's own answer
  (`affordable_with_plan/full_payment`, needing `reduce_to:event_989:665950`) is now the *more conservative*
  reading; our model, once it trusts the anchored base salary, finds the full amount safe without any
  spending change at all. Same "no spending change needed" logic as `request_06` explains why no
  `spending_changes_needed` appears.
- **`request_08`** — `message_06` ("Your next salary is reduced to EUR 1422.85 ... due to approved unpaid
  leave") is correctly extracted as `income_reduction`, but the value (1422.85) is exactly what Phase 2's own
  recurrence detector had *already* independently derived from the settled-event history for this user. The
  evidence confirms the existing estimate rather than changing it, so the decision is byte-for-byte identical
  before and after evidence integration (`not_affordable/not_recommended` both times). `amount_safe_to_pay`
  (291.125) is close to the sample's (284.57); the residual gap is a minor forecast difference that evidence
  integration has no new information to close, since the message added nothing the model didn't already know.
- **`request_03` / `event_253`** — `image_01` resolves `event_253`'s blank salary amount to exactly
  4,365,000 IDR, matching the amount Phase 2's recurrence detector had already inferred for this user's other
  salary occurrences. `unresolved_amount_events` for `user_03` goes from `['event_253']` to `[]`, and
  recurrence detection is re-run and finds 6 patterns (up from 5, since the previously-blank event can now
  anchor an additional occurrence). `affordability_status`/`recommended_payment_method` match the sample
  exactly both before and after (`affordable_later/wait`); only `amount_safe_to_pay` and
  `earliest_date_for_full_payment` carry the same residual estimation gap seen throughout the project.

## Spending changes across the 25 samples

None of the 25 sample requests end up needing a spending change in the generated decision — in every case
where the sample itself used one (`request_06`, `request_11`), our post-evidence model already finds enough
headroom without it (see above). The spending-change mechanism itself is exercised and independently
verified elsewhere in the 250-request evaluation set (5 of the 250 requests use one — see
`eval/phase5_report.md`) and in `code/tests/test_spending_changes.py`'s dedicated `RealCaseTests`.

No rule was loosened or tightened to chase a higher match count; every number above comes directly from
running `code/main.py`'s Phase 5 diagnostics / this same comparison script against the real dataset.
