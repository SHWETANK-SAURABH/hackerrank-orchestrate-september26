# Phase 2 Report — Financial State + Recurrence + Conflict Resolution

**Scope (per `implementation/phase2.md`):** `code/financial_state.py` only — currency-normalized event classification, linked-event validation/labelling, conservative recurrence detection, and per-user `FinancialState` assembly. No 90-day forecasting, `amount_safe_to_pay`, payment-plan generation, spending-change optimization, plan ranking, `output.csv` generation, or LLM/vision calls were added, per the explicit exclusions.

> **Addendum (added during Phase 3 real-data validation):** Cross-checking Phase 3's forecast against the 25 solved `sample_requests.csv` examples surfaced two real bugs in `detect_recurrence_patterns`, both fixed retroactively in `financial_state.py` (see `eval/phase3_report.md` for the full investigation):
> 1. **New-employee income gap.** A user with a `scheduled` "next confirmed salary" but fewer than 3 prior settled salary payments (3 of 275 users) got *zero* recurring income projected, causing their 90-day forecast to run out of money even though the sample answer (`request_01`) assumes salary continues. Fixed by anchoring a recurring pattern on the confirmed scheduled event itself when normal detection can't clear its evidence bar (`_anchor_confirmed_income_pattern`).
> 2. **Outlier-poisoned cadence detection.** A handful of one-off, differently-timed same-category entries (a bonus, an arrears correction) previously invalidated an *entire* otherwise-clean recurring pattern (e.g. `request_03`/`request_04`'s users had 5-7 clean monthly salary payments plus 1-2 one-off extras, and got no pattern at all). Fixed by extracting the longest run of occurrences that agree on one cadence (`_longest_dominant_cadence_run`) instead of requiring the whole group to be uniformly regular.
>
> Also fixed: `round()`'s banker's-rounding behavior (`round(30.5) == 30`) was silently shifting projected dates a day early; replaced with explicit round-half-up (`_round_half_up`) everywhere a mean interval is converted to a calendar offset.
>
> All Phase 2 tests (unchanged) still pass after these fixes. Re-running the aggregate statistics: the event-bucket totals below (effective/pending/future/cancelled/non-cash/unresolved, all summing to exactly 25,342) are **unchanged** — only recurrence detection improved: recurring income patterns rose from 201 to **225** (+24) and recurring expense patterns from 2,108 to **2,119** (+11), with the confidence mix shifting from {high: 2,283, medium: 26} to {high: 2,292, medium: 52} (more patterns now correctly recovered, several at "medium" confidence since they rely on a shorter or partly-synthesized run of evidence). The tables below are left as originally reported rather than silently rewritten; treat the numbers above as the current, corrected state.

---

## Files Created

| File | Purpose |
|---|---|
| `code/financial_state.py` | `EventTreatment` (10-way per-row cash-flow classification), `LinkedRelationship` (6-way linked-pair labelling), `ConflictTier`/`resolve_conflict` (generic 4-tier conflict resolver), `RecurrenceFrequency` + `detect_recurrence_patterns` (conservative recurrence detection), `NormalizedEvent`/`RecurrencePattern`/`ConflictResolutionRecord`/`FinancialState` dataclasses, `build_financial_state(store, converter, user_id)`, `build_all_financial_states(...)`. |
| `code/tests/test_financial_state.py` | 41 tests: currency/amount handling, status classification, linked-event resolution (incl. cross-user and invalid-reference detection), conflict resolution (all 4 tiers + 2 real-shape demonstrations), recurrence detection (positive, one-off, irregular, cancelled-evidence exclusion), profile-policy preservation, and a real-dataset integration suite. |
| `eval/phase2_report.md` | This report. |

## Files Modified

| File | Change |
|---|---|
| `code/main.py` | Added the Phase 2 smoke-test section (`_run_phase2_financial_state_validation` and its helpers), appended after the existing, unmodified Phase 1 smoke test. Removed one unused import (`CurrencyConversionError`, `EventTreatment`) left over from drafting. Phase 1's functionality is otherwise untouched. |

`code/data_loader.py` and `code/currency.py` were **not modified** — Phase 2 consumes their public API only (`DataStore.get_profile`, `.get_events_for_user`, `.get_event`, `.profiles_by_user_id`; `CurrencyConverter.convert`/`.get_rate`), per the instruction not to rewrite working Phase 1 functionality.

No file under `dataset/` was created, modified, or deleted, and `output.csv` (root) was not created.

---

## Tests Executed

```bash
python -m unittest discover -s code/tests -p "test_*.py" -v
```

**Result: 79 / 79 passed, 0 failed, 0 errors** — all 38 Phase 1 tests still pass unchanged, plus 41 new Phase 2 tests.

The 18 required cases from `implementation/phase2.md` Part 13, and where each is covered:

| # | Requirement | Test(s) |
|---|---|---|
| 1 | Home-currency event unchanged | `test_home_currency_event_remains_unchanged` |
| 2 | Foreign-currency event converted using settlement date | `test_foreign_currency_event_is_converted_using_settlement_date` (uses a fixture where `event_date` and `settlement_date` deliberately differ and only the settlement-date rate exists, so using the wrong date would fail the test) |
| 3 | Missing amount stays `None` | `test_missing_amount_remains_none`, `test_no_unresolved_blank_amount_is_converted_to_zero` |
| 4 | Failed expense not effective | `test_failed_expense_does_not_become_effective` |
| 5 | Cancelled transaction not effective | `test_cancelled_transaction_does_not_become_effective_cash_flow` |
| 6 | Pending credit not available cash | `test_pending_credit_is_not_considered_available_cash` |
| 7 | Future scheduled income is future income | `test_future_scheduled_income_is_future_income_not_current_cash` |
| 8 | Linked event resolved via `event_id` | `test_linked_event_is_resolved_through_event_id` |
| 9 | Invalid `linked_event_id` detected | `test_invalid_linked_event_id_is_detected` |
| 10 | Cross-user `linked_event_id` detected | `test_cross_user_linked_event_id_is_detected` |
| 11 | Recurring income pattern detected with clear evidence | `test_recurring_income_pattern_is_detected_with_clear_repeated_evidence` |
| 12 | Recurring expense pattern detected with clear evidence | `test_recurring_expense_pattern_is_detected_with_clear_repeated_evidence` |
| 13 | One-off transaction not classified as recurring | `test_one_off_transaction_is_not_classified_as_recurring`, `test_two_occurrences_are_not_enough_to_be_recurring`, `test_irregular_spacing_is_not_classified_as_recurring`, `test_cancelled_occurrences_are_not_used_as_recurrence_evidence` |
| 14 | Flexibility + `minimum_allowed_amount` preserved | `test_flexibility_and_minimum_allowed_amount_are_preserved` |
| 15 | Protected/reducible/stoppable categories preserved | `test_protected_reducible_stoppable_categories_are_preserved` |
| 16 | Current profile balance is the starting balance | `test_current_profile_balance_remains_the_starting_balance`, `test_balance_matches_profile_not_reconstructed_from_events` |
| 17 | No blank amount converted to zero | `test_no_unresolved_blank_amount_is_converted_to_zero`, `test_blank_amount_events_land_in_unresolved_bucket_not_zeroed` |
| 18 | Conflict-resolution precedence is deterministic | `test_tier1_..4_*`, `test_conflict_resolution_is_deterministic_across_repeated_calls`, `test_single_candidate_is_trivially_its_own_winner`, `test_empty_candidate_list_raises` |

**Tricky linked-event fixture** (explicitly requested): `test_tricky_linked_scenario_naive_void_of_original_would_be_wrong` builds a settled EUR 500 expense linked *from* a later pending refund. A naive implementation that assumed "`linked_event_id` present ⇒ void the earlier event" would drop a real expense from the ledger; the test asserts the original stays fully effective and the refund is only `PENDING_INCOME` (not available cash) until it settles — mirroring the 8 real `expense(settled)→refund(pending)` pairs found in the dataset.

Two additional tests (`test_resolve_conflict_on_the_real_cancelled_then_replaced_shape` and `test_resolve_conflict_would_misjudge_the_failed_then_rescheduled_pair`) exist specifically to document a design finding described below.

---

## Design Finding: Conflict Resolution Is Implemented But Never Auto-Fires On This Dataset

`resolve_conflict()` implements the full 4-tier priority from the problem statement (explicit status → newer record → settled-over-estimate → safer interpretation) and is directly unit-tested on all 4 tiers plus determinism and edge cases. While building `build_financial_state`, I initially wired it to fire automatically on the two `linked_event_id` relationship kinds that look like "the same slot told twice" (`REPLACEMENT_AFTER_CANCELLATION`, `RESCHEDULED_AFTER_FAILURE`) — and this surfaced a real design bug during testing: applying `resolve_conflict` to a real `failed → rescheduled` debt-payment pair declares the **failed** attempt the "winner" (since `failed` is as explicit/definitive a status as `settled`, and the retry is a mere `scheduled` estimate under tier 1). That is the wrong takeaway if used to decide what belongs in the ledger — the live, forward-looking fact is the rescheduled payment, not the dead attempt.

The underlying reason: neither of those two patterns is actually a *conflict*. A cancelled authorization followed by its settled replacement, and a failed payment followed by its rescheduled retry, are pairs of **independently true sequential facts** — not two competing descriptions of one fact — exactly like a refund is independent of its original expense. Each row's own `status`/`direction` already gives it the correct, independent `EventTreatment` (see `classify_treatment`'s docstring: "the link alone does not determine whether a row counts toward cash flow"). So `build_financial_state` does **not** call `resolve_conflict` for any of the dataset's linked patterns — confirmed empirically: **0 conflict-resolution records across all 275 users** (`test_no_real_user_produces_a_conflict_resolution_record`). `resolve_conflict` and `FinancialState.conflict_resolutions` remain fully implemented and available for Phase 3, where message/image evidence can create genuine same-fact conflicts (e.g. a message amending an amount the ledger already recorded differently) — this dataset's `financial_events.csv` alone never does.

---

## Smoke-Test Results (`python code/main.py`)

Exit code: **0**. Phase 1's output is unchanged; the new Phase 2 section:

```
Phase 2: FinancialState diagnostics for representative users
--------------------------------------------------------------
  user user_01: ZAR, balance=58481.1, effective=100, unresolved=0,
                recurring_income=0, recurring_expense=9, pending=1,
                future_income=1, future_expense=0, cancelled/failed=1,
                non_cash=0, linked_issues=0, conflicts=0, warnings=0
  user user_03: IDR, ... unresolved=1 (event_253, blank August 2019 salary)
  user user_25: IDR, ... recurring_income=1 (foreign-currency USD salary, normalized)
  user user_55: INR, ... future_expense=1, cancelled/failed=1

Recurring pattern from real history (user user_01)
  debit/debt_repayment ('Education loan instalment'): monthly, ~every 30.8 days,
  typical 3487 ZAR, 5 occurrences, confidence=high
  next_expected_occurrence = 2024-03-13

Linked-event resolution (user user_01)
  event event_99 (refund/settled) links to event_98
    original: event_98 (expense/settled)
    relationship = refund_of_expense
    issues       = none

Building FinancialState for every user (no crashes, no zeroed blanks)
  built FinancialState for 275 users
  total unresolved-amount events across all users = 16

PHASE 2 SMOKE TEST: SUCCESS
(no affordability/forecast/payment-plan logic ran; output.csv was not touched)
```

No assertion failures (each unresolved-amount event is asserted `amount_home_currency is None` inline in the smoke test itself).

---

## Aggregate Statistics (all 275 users, real dataset)

### 3. Users whose FinancialState was successfully built
**275 / 275** — every user with a profile, with zero exceptions (`test_builds_state_for_every_user_without_error`).

### 4. Effective events (settled, known amount)
**25,136** — every event is accounted for exactly once across all buckets:

| Bucket | Count |
|---|---|
| Effective (income + expense) | 25,136 |
| Pending | 69 |
| Future/scheduled income | 47 |
| Future/scheduled expense | 21 |
| Cancelled or failed | 43 |
| Non-cash (investment valuation) | 10 |
| Unresolved amount | 16 |
| **Total** | **25,342** (matches `financial_events.csv` row count exactly) |

Cross-checks against Phase 0/1 findings: cancelled+failed = 22+21 = **43** ✓; non-cash = the 10 `unrealized` rows ✓; pending (69) = the 71 raw `pending` rows minus 2 whose amount is also blank (those land in "unresolved" instead, not double-counted) ✓; future/scheduled income (47) matches the 47 users with exactly one scheduled salary row ✓; future/scheduled expense (21) = 23 raw scheduled-debit rows minus 2 blank-amount ones ✓.

### 5. Unresolved blank-amount events
**16**, matching Phase 0/1 exactly (`event_253`, `event_1442`, ... `event_10521`) — none converted to zero, none guessed at.

### 6. Recurring income patterns
**201** detected across all users (one pattern per `(user, event_type, direction, category)` group meeting the evidence bar). Not every user has enough salary history in their visible event window to clear the 3-occurrence threshold (e.g. `user_01` has only 1 visible salary row and correctly gets 0 recurring-income patterns).

### 7. Recurring expense patterns
**2,108** detected across all users (rent, utilities, subscriptions, debt repayment, and variable-but-periodic categories like dining/groceries/transport all qualify when the evidence is regular enough).

Combined pattern confidence: **2,283 "high"**, **26 "medium"** (of 2,309 total). Frequency mix: **1,805 monthly, 279 weekly, 225 biweekly** — no quarterly/annual patterns were detected, consistent with each user's visible event-history window being roughly 6 months (Phase 0 finding), too short to exhibit a quarterly/annual cadence with 3+ occurrences.

### 8. Pending events
**69** (63 pending expenses + 8 pending refunds, minus the 2 also-blank-amount ones now counted under "unresolved" instead — see the effective-events table above for the exact reconciliation).

### 9. Scheduled/future income events
**47** — exactly one per user, for the 47 users who have a "next confirmed salary" row, matching Phase 0/1 exactly.

### 10. Linked-event validation results
All **58** `linked_event_id` rows in `financial_events.csv` were resolved and classified with **zero** `UNKNOWN_PATTERN` results and **zero** structural issues (no missing reference, no cross-user reference, no out-of-order reference — `linked_issue_total = 0`):

| Relationship | Count |
|---|---|
| `refund_of_expense` | 22 |
| `replacement_after_cancellation` | 8 |
| `investment_valuation_update` | 10 |
| `rescheduled_after_failure` | 7 |
| `investment_sale` | 5 |
| `followup_or_split_payment` | 6 |
| **Total** | **58** (matches the dataset exactly) |

The negative-path checks (missing reference, cross-user reference, out-of-order reference) have no real-data examples to exercise, so they are covered by synthetic fixtures instead (`test_invalid_linked_event_id_is_detected`, `test_cross_user_linked_event_id_is_detected`, `test_out_of_order_linked_reference_is_detected`).

### 11. Conflict-resolution behavior implemented
`resolve_conflict()` implements and unit-tests all 4 priority tiers (see the "Design Finding" section above for why it is not wired into automatic per-linked-pair resolution for this dataset). **0** conflict-resolution records were produced across all 275 real users — an expected, verified result (`test_no_real_user_produces_a_conflict_resolution_record`), not a gap: this dataset's `financial_events.csv` never contains two records genuinely competing to describe the same fact. The mechanism remains available and tested for Phase 3.

### 12. Data-quality warnings
**0** across all 275 users (`warning_total = 0`) — no unrecognized status/direction combination, no currency-conversion failure. `classify_treatment` and `normalize_event` both have a defensive fallback path (`EventTreatment.UNKNOWN`, degrade-to-`UNRESOLVED_AMOUNT` on a currency-conversion error) that is implemented and unit-tested (`test_unrecognized_combo_is_flagged_not_guessed`) but never triggers on the real data, since Phase 1's `DataStore.load(strict=True)` already guarantees every status/direction/currency value is one of the known ones before Phase 2 ever runs.

### 13. Confirmation: `dataset/` was not modified
Verified with `git status --porcelain` and `git diff --stat -- dataset/` after all implementation, test, and smoke-test runs: **zero changes under `dataset/`**. `output.csv` was not created at the repository root.

### 14. Confirmation: no affordability/forecast/payment-plan logic was added
`financial_state.py` contains no reference to `amount_safe_to_pay`, a 90-day forecast loop, payment-plan generation, spending-change optimization, or plan ranking. `build_financial_state` stops at producing the reconciled `FinancialState` — the next phase (forecasting) has not been started.

---

## Performance

Each `build_financial_state(store, converter, user_id)` call uses only `store.get_events_for_user(user_id)` — an O(1) dict lookup returning that user's own ~92 events on average — never a scan of the full 25,342-row table. Building `FinancialState` for **all 275 users** (`_run_phase2_financial_state_validation` in the smoke test) completes in well under a second as part of `python code/main.py`'s total run.

## Determinism

`resolve_conflict` is a pure function over its input list (verified by `test_conflict_resolution_is_deterministic_across_repeated_calls`); `detect_recurrence_patterns` groups and sorts deterministically (`sorted(groups.items())`, events sorted by `(date, event_id)`); no randomness, wall-clock, or LLM call appears anywhere in the module.

---

## Recommended Next Phase

**Phase 3: `forecast.py`** — the 90-day balance simulation, built on top of `FinancialState`'s `effective_events` (as the historical baseline), `recurring_income_patterns` / `recurring_expense_patterns` (for projecting forward), `future_income_events` / `future_expense_events` (known future cash movements), and `pending_events` (near-term reservations) — still with no `amount_safe_to_pay` or affordability decision yet, per the Phase 0 report's recommended order.

---

Stopping here, per `implementation/phase2.md`. Phase 3 was not started automatically.
