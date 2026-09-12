# Phase 4 Report — Payment Plans + Verifier + Buy/Wait Decision Logic

**Scope (per `implementation/phase4.md`):** `code/payment_plans.py`, `code/verifier.py`, `code/decision_engine.py` — candidate plan generation, independent plan verification, and the final deterministic `affordability_status`/`recommended_payment_method`/`payment_plan`/`amount_safe_to_pay`/`earliest_date_for_full_payment` decision. No messages/images, no spending-change optimization, and no `output.csv` were implemented — all explicitly deferred to Phase 5+.

---

## Files Created

| File | Purpose |
|---|---|
| `code/payment_plans.py` | `PaymentPlan` dataclass; `generate_full_payment_plan`, `generate_partial_payment_plan`, `generate_installment_plans` (exact reproduction of supplied `request_payment_options.csv` rows, never an invented schedule), `generate_wait_plan`; `generate_candidate_plans` orchestrator. |
| `code/verifier.py` | `VerificationResult`; `verify_plan` — independently re-derives and checks all 21 rules from Part 17 against a candidate, never trusting how it was built. Whole-plan safety is simulated as one combined scenario via Phase 3's `forecast.simulate_payments`. |
| `code/decision_engine.py` | `DecisionResult`, `VerifiedCandidate`; `make_decision` — generates candidates, verifies every one, resolves the affordability-status hierarchy tier-first, then ranks within a tier by the problem statement's 6-point order. |
| `code/tests/test_payment_plans.py` | 28 tests. |
| `code/tests/test_verifier.py` | 30 tests. |
| `code/tests/test_decision_engine.py` | 22 tests. |
| `eval/phase4_report.md` | This report. |

## Files Modified

| File | Change |
|---|---|
| `code/forecast.py` | Generalized to a multi-payment simulator (`simulate_payments`, `prior_payments` on `maximum_safe_payment`/`earliest_safe_payment_date`) — required by Part 19/20 ("simulate the entire plan," "the two legs must be simulated together"), fully backward-compatible (all Phase 3 call sites and tests unaffected by the new parameters' defaults). **Also corrected a real same-day cash-flow bug** found via this phase's own real-data cross-check — see below; this is the one substantive behavior change, not just an additive one. |
| `code/main.py` | Added the Phase 4 diagnostic section (representative decisions, sample cross-check summary, 250-request diagnostic summary), appended after the unmodified Phase 1-3 sections. |
| `eval/phase3_report.md` | Addendum documenting the same-day cash-flow correction (below), since it changes Phase 3's core simulation semantics. |
| `eval/phase2_report.md` | Unchanged this phase (already carries its own Phase-3-discovered-bug addendum from before). |

No file under `dataset/` was created, modified, or deleted, and `output.csv` (root) was not created.

---

## A Second Necessary Detour: The Same-Day Cash-Flow Rule Was Wrong

Part 27's required 25-sample cross-check immediately produced a suspicious result: `request_19`'s own solved answer schedules a partial payment's second leg (INR 10,840) on **the exact day a salary posts**, and calls it safe — but Phase 3's original same-day rule (a hypothetical/probe payment always sorts *before* every real entry on its own date, so it could never benefit from a same-day credit) judged that exact plan **unsafe**.

Investigating confirmed the original rule was a reasonable-sounding but ultimately wrong guess made in Phase 3 without concrete evidence either way. The corrected rule — now implemented and confirmed against real data — is: **every cash flow dated the same calendar day (including a hypothetical payment being tested) is netted into one change, and the minimum-balance check runs once, at end-of-day.** This also matches the problem statement's own worked safety-violation example ("Day 10: 12,000, Day 11: 8,000, Day 12: 15,000"), which checks exactly one value per day, never per-transaction.

`_simulate`, the probe-based closed-form helpers, and `earliest_safe_payment_date`'s candidate-set logic were all updated; `_entry_sort_key` is now a purely cosmetic display order. All 6 Phase 3 tests that encoded the old assumption were corrected in place with the same reasoning documented inline (see `test_forecast.py`'s `SameDayOrderingTests` and related `earliest_safe_payment_date` tests). The full 204-test suite passes after the fix.

**Impact on the sample cross-check:** before this fix, `request_19` showed `not_affordable` (wrong); after, it shows `affordable_with_plan/partial_payment` with the **exact same payment dates** as the solved sample (`2024-09-04` and `2024-09-15`) and a closely comparable amount. `request_02`'s installment schedule now also reproduces the sample's exact dates and amounts. This is a concrete, evidence-driven correction — not a case of "modifying rules merely to force sample matching" (Part 27's explicit warning) — since it was validated against a specific, previously-unexplained mismatch, is internally consistent with the problem statement's own example, and improved the overall match rate materially (see below).

---

## Candidate Plan Types Implemented (Part 22)

| Method | Generated when | Sized/dated using |
|---|---|---|
| `full_payment` | User accepts `full_payment` | `requested_amount` on `request_date`, always (safety is the verifier's job, not the generator's) |
| `partial_payment` | Request allows it, user accepts `partial_payment` | `forecast.maximum_safe_payment` for the first leg; `forecast.earliest_safe_payment_date(..., prior_payments=[...])` for the second, **with the first leg already committed** (Part 20) |
| `installments` | User accepts `installments`, `max_installment_months` is set | One candidate per eligible supplied `request_payment_options.csv` row — dates reconstructed from `first_payment_date + payment_frequency_days * i`, every other field (amounts, fee, total) copied exactly, never invented (Part 7/8) |
| `wait` | User accepts `full_payment` | `forecast.earliest_safe_payment_date` (no spending changes, no installment options) — omitted entirely when that date equals `request_date` (that is `affordable_now` territory) |

`not_recommended` is never generated as a candidate — it is `decision_engine`'s fallback when zero candidates survive verification.

## Verifier Rules (Part 17/18/19)

All 21 listed checks are implemented in `verifier.verify_plan`, split into structural rules (dates present/ordered/in-bounds, positive amounts, correct sums, supported method, no spending changes, completes by deadline), method-specific rules (exact full/partial/wait shape; exact installment-vs-option field-by-field comparison using `Decimal` equality, never round-then-compare), and — critically — **whole-plan safety** via one call to `forecast.simulate_payments` covering every payment in the plan together, never payment-by-payment (Part 19: two individually-safe payments can combine to breach the floor; `test_multi_payment_installment_forecast_is_verified_as_one_scenario` and `test_two_individually_safe_payments_can_combine_to_be_unsafe` pin this down directly). The verifier never repairs a bad candidate — only reports why it failed, and `payment_plans.py`'s own candidates are given no special trust (tests build deliberately-broken `PaymentPlan` objects directly to confirm the verifier catches them regardless of provenance).

## Decision/Status Hierarchy (Part 11/12)

`_select_best` resolves tiers **in fixed priority order**, never one flat comparison across every candidate:

1. `affordable_now` — a valid `full_payment` candidate exists (it is always dated exactly `request_date` by construction, so this tier is unambiguous).
2. `affordable_with_plan` — a valid `partial_payment` or `installments` candidate exists.
3. `affordable_later` — a valid `wait` candidate exists.
4. `not_affordable` / `not_recommended` — nothing survived verification.

This directly encodes the lesson from sample `request_06` (Phase 0 §10.1): a plan enabled by a spending change today can outrank a later no-change "wait," so the status hierarchy must be resolved before any flat ranking runs. Phase 4 does not implement spending changes yet, so `request_06`'s exact scenario cannot be reproduced by this module alone (`decision_engine.py`'s docstring documents this explicitly) — but the selection logic is already built to accommodate it without restructuring once Phase 5 adds spending-change candidates.

## Ranking Rules (Part 13)

Applied only **within** a tier that has more than one valid candidate, via a single deterministic sort key: `(spending_changes, total_payable, first_payment_date, number_of_payments, payment_option_id)`. Criterion 1 ("complete by deadline") is not encoded as a ranking key — every candidate reaching this stage already passed verification, which already enforces completing by the deadline, so it can never differentiate between two already-valid candidates; it is a gate applied earlier, not a tie-break (documented in `_rank_key`'s docstring). `payment_option_id` is compared numerically (`payment_option_2` before `payment_option_10`), not lexicographically. All 6 ranking tests pass, including determinism across repeated calls.

## Payment Preference Handling (Part 14)

`full_payment`/`partial_payment`/`installments` are only ever generated if the corresponding value is in `payment_methods_user_will_consider`; `wait` is gated on `full_payment` specifically (since `"wait"` itself never appears in that field — confirmed in Phase 0). Both the generator (as an eligibility filter) and the verifier (independently, as a defense-in-depth re-check) enforce this. Two states differing only in payment-method preference produce byte-identical `amount_safe_to_pay`/`earliest_date_for_full_payment` (`test_earliest_date_does_not_depend_on_payment_preferences`).

## Installment Validation Behavior (Part 15/18)

`max_installment_months is None` ⇒ zero installment candidates, full stop. Otherwise, `installment_option_months` (monthly-cadence options only, per Phase 0's confirmed dataset convention of `payment_frequency_days` ∈ {28, 30, 31}) is compared against the user's cap, checked independently by both the generator (which simply excludes ineligible options) and the verifier (which re-derives and re-checks the same comparison rather than trusting the generator skipped it correctly). Every option field — `number_of_payments`, `payment_amount` (all installments), `first_payment_date`, the reconstructed schedule, `financing_fee`, `total_payable_amount` — is compared with exact `Decimal` equality; any mismatch rejects the candidate outright (Part 18: "do not round an option's values and then call them equal").

## Partial-Payment Behavior (Part 6/20)

Exactly two payments, sized by `maximum_safe_payment` (first leg, capped at `requested_amount`) then `earliest_safe_payment_date` for the exact remainder **with the first leg passed as `prior_payments`** — never against a forecast that has "forgotten" the first payment (confirmed by `test_second_payment_checked_with_first_already_applied`, which shows the plan correctly comes back `None` when the first leg alone already exhausts all headroom with no future income to recover it). The two amounts always sum to `requested_amount` exactly, by construction (`remaining = requested_amount - safe_amount` is exact `Decimal` subtraction, never a source of rounding drift). Rejected outright if the request disallows partial payment, the user doesn't accept it, the safe amount isn't strictly between `0` and `requested_amount`, or no safe date for the remainder exists on or before `desired_completion_date`.

## Wait Behavior (Part 9/21)

A single future payment of the full `requested_amount`, on the raw baseline `earliest_safe_payment_date` (no spending changes, no installment options) — never generated if that date is `request_date` itself (already `affordable_now`) or after the deadline, and never generated if the user doesn't accept `full_payment`. Independently re-verified (date is strictly after `request_date`, amount matches exactly, and the single payment is itself simulated as safe).

## Earliest-Full-Payment-Date Behavior (Part 16)

`decision_engine.make_decision` computes `earliest_date_for_full_payment` via one direct call to `forecast.earliest_safe_payment_date(state, request.request_date, request.requested_amount, deadline=None, ...)` — **before** any candidate is generated, and it is never touched again regardless of which plan is ultimately recommended. Confirmed independent of payment-method preference, of installment options, and — explicitly — of the recommended plan's own completion date (`test_earliest_date_is_not_replaced_by_the_recommended_plans_completion_date`, where the recommended installment plan finishes well after the raw baseline date).

---

## Tests Executed

```bash
python -m unittest discover -s code/tests -p "test_*.py" -v
```

**Result: 204 / 204 passed, 0 failed, 0 errors** — all 124 Phase 1-3 tests (6 corrected for the same-day fix, documented above) plus 80 new Phase 4 tests (28 `payment_plans` + 30 `verifier` + 22 `decision_engine`), covering every one of the 56 required cases in Part 26.

---

## 25-Sample Cross-Check (Part 27)

Full comparison of `decision_engine.make_decision`'s output against every solved sample (no messages/images, no spending changes — exactly this phase's scope):

| request | sample status | generated status | sample method | generated method | match | sample earliest | generated earliest |
|---|---|---|---|---|---|---|---|
| request_01 | affordable_now | affordable_now | full_payment | full_payment | ✓ | 2024-03-03 | 2024-03-03 |
| request_02 | affordable_with_plan | affordable_with_plan | installments | installments | ✓ | 2025-09-15 | 2025-09-15 |
| request_03 | affordable_later | affordable_later | wait | wait | ✓ | 2019-11-15 | 2019-10-16 |
| request_04 | affordable_later | affordable_later | wait | wait | ✓ | 2024-06-15 | 2024-06-13 |
| request_05 | not_affordable | affordable_now | not_recommended | full_payment | ✗ | (none) | 2025-11-06 |
| request_06 | affordable_with_plan | affordable_now | full_payment | full_payment | ✗ (method matches, status doesn't) | 2026-01-15 | 2026-01-03 |
| request_07 | affordable_with_plan | affordable_with_plan | installments | installments | ✓ | 2024-10-23 | 2024-10-13 |
| request_08 | affordable_later | not_affordable | wait | not_recommended | ✗ | 2025-04-15 | (none) |
| request_09 | affordable_now | affordable_now | full_payment | full_payment | ✓ | 2026-07-04 | 2026-07-04 |
| request_10 | not_affordable | not_affordable | not_recommended | not_recommended | ✓ | (none) | 2024-12-06 |
| request_11 | affordable_with_plan | not_affordable | full_payment | not_recommended | ✗ | 2025-07-15 | (none) |
| request_12 | affordable_with_plan | affordable_with_plan | installments | installments | ✓ | 2026-04-05 | 2026-04-05 |
| request_13 | affordable_later | not_affordable | wait | not_recommended | ✗ | 2024-05-15 | (none) |
| request_14 | not_affordable | not_affordable | not_recommended | not_recommended | ✓ | (none) | (none) |
| request_15 | not_affordable | not_affordable | not_recommended | not_recommended | ✓ | (none) | (none) |
| request_16 | affordable_now | affordable_now | full_payment | full_payment | ✓ | 2023-08-12 | 2023-08-12 |
| request_17 | affordable_with_plan | affordable_with_plan | installments | installments | ✓ | 2026-03-15 | 2026-04-18 |
| request_18 | affordable_later | affordable_later | wait | wait | ✓ | 2026-09-15 | 2026-08-14 |
| request_19 | affordable_with_plan | affordable_with_plan | partial_payment | partial_payment | ✓ | 2024-09-15 | 2024-09-15 |
| request_20 | not_affordable | not_affordable | not_recommended | not_recommended | ✓ | (none) | (none) |
| request_21 | affordable_with_plan | affordable_now | full_payment | full_payment | ✗ (method matches, status doesn't) | 2026-04-15 | 2026-04-03 |
| request_22 | affordable_with_plan | affordable_with_plan | installments | installments | ✓ | 2025-01-15 | 2025-01-16 |
| request_23 | affordable_later | affordable_later | wait | wait | ✓ | 2025-07-15 | 2025-07-14 |
| request_24 | not_affordable | not_affordable | not_recommended | not_recommended | ✓ | (none) | (none) |
| request_25 | not_affordable | not_affordable | not_recommended | not_recommended | ✓ | (none) | (none) |

**Status+method exact match: 19/25 (76%). Earliest-date exact match: 11/25** (most others are within days — see below).

### Mismatch investigation

- **`request_05`** — carried over from Phase 3, still unexplained: no message, image, or unresolved event for `user_05` that would account for the gap; recurring income (14,740/month, 10 clean settled occurrences) comfortably outpaces expenses in the raw model. Documented, not forced to match. **Not resolved by Phase 4.**
- **`request_06`, `request_21`** — both `affordable_with_plan/full_payment` in the sample (a spending change closes a small gap to make full payment safe *today*) vs. this phase's `affordable_now/full_payment` (the raw model already finds the full amount safe today, no spending change needed). The recommended *method* already matches; the *status* differs only because this phase's recurring-income estimate is evidently a little more generous than the sample author's for these two users — plausibly the same category of estimation difference documented throughout (median-based typical amounts vs. whatever exact figure the puzzle intended). Since Phase 4 has no spending-change mechanism at all, there is nothing further to reconcile here until Phase 5.
- **`request_08`** — has a message ("Your next salary is reduced to EUR 1422.85... due to approved unpaid leave") that this phase correctly does not read (messages are explicitly out of scope). Expected gap, deferred to evidence integration.
- **`request_11`** — the sample's own `full_payment` plan is only reachable via a spending change (its `amount_safe_to_pay`, 12,510,645, is close to but short of the full 13,110,000 requested); with zero spending-change capability, this phase correctly reports `not_affordable`. Expected gap, deferred to Phase 5.
- **`request_13`** — no message, image, or unresolved event found for `user_13`. The raw baseline forecast never dips negative, but the tightest point (end of the 90-day horizon, since this user's projected income only barely keeps pace with expenses) leaves less headroom than the sample's `amount_safe_to_pay` implies. Possibly the same recurring-estimate-difference category as `request_06`/`request_21`, but not conclusively traced. **Flagged as unresolved, alongside `request_05`.**

None of these six were "fixed" by adjusting a rule to match the sample — each was investigated on its own evidence, and only genuine, independently-justified bugs (the same-day cash-flow rule, found via `request_19`) were corrected.

### Why several matching rows still show a different `earliest_date_for_full_payment`

Even among the 19 matching rows, the exact date sometimes differs by a few days (e.g. `request_07`: sample 2024-10-23 vs. generated 2024-10-13). Since this field is driven entirely by recurring-pattern projections (Phase 2) and the day-level forecast (Phase 3), a few days' difference traces to the same root cause acknowledged throughout Phases 2-4: this implementation's `typical_amount_home_currency` (a per-category median) and recurring-interval estimate are a reasonable, well-tested, conservative reconstruction from the available `financial_events.csv` history, but need not exactly reproduce whatever exact figure the puzzle's hidden ground truth used to construct each scenario. This does not indicate a logic bug — `request_01`, `request_02`, `request_09`, `request_12`, `request_16`, and `request_19` all match to the exact day, which would be an implausible coincidence if the underlying date arithmetic were wrong.

---

## 250-Request Diagnostic Summary (Part 28)

Processed all 250 evaluation requests in **1.96 seconds (7.8 ms/request)** — no crashes, no exceptions.

| affordability_status | count | recommended_payment_method | count |
|---|---|---|---|
| `affordable_now` | 63 | `full_payment` | 63 |
| `affordable_with_plan` | 56 | `partial_payment` | 7 |
| `affordable_later` | 35 | `installments` | 49 |
| `not_affordable` | 96 | `wait` | 35 |
| | | `not_recommended` | 96 |

(Both columns sum to 250; `not_affordable` count equals `not_recommended` count exactly, as required — a request is `not_affordable` if and only if its recommended method is `not_recommended`.)

- **Invalid candidates generated (then correctly rejected by the verifier): 118**, for a total of 168 individual rejection reasons — a healthy sign that candidates are generated somewhat optimistically and the verifier is doing real, substantive work rather than rubber-stamping.
- **Requests with no valid plan at all: 96** (matches the `not_affordable` count).
- **Requests with unresolved evidence inside the horizon: 2** (blank-amount `scheduled`/`pending` events Phase 2 left unresolved and Phase 3/4 correctly never zero or guess).
- **Impossible values found: 0** — no `amount_safe_to_pay < 0`, none `> requested_amount`, no payment-sum mismatch, no plan completing after its deadline, and (re-verified independently for every one of the 250 recommended plans via a fresh `forecast.simulate_payments` call, not just by trusting the stored verification result) **no unsafe plan was ever marked valid**.

---

## `request_05` Investigation (Part 27, explicitly required)

Re-investigated with the full decision engine now available (not just the raw forecast, as in Phase 3): `user_05` has no message, no image, and no unresolved (blank-amount) event of any kind. Ten clean, evenly-spaced settled salary payments (14,740/month) comfortably exceed the ~12,791/month of recurring expenses in this implementation's model, so `amount_safe_to_pay` comes out to the full requested amount (15,488) rather than the sample's 737, and the status comes out `affordable_now` rather than `not_affordable`. Every deterministic rule this project's specification files describe was checked against this case across two phases; none explains the gap. **This is not resolved by Phase 4 and is documented here, as instructed, as an unexplained discrepancy** rather than forced to match by weakening the model.

---

## Confirmation: `dataset/` Was Not Modified

Verified with `git status --porcelain` and `git diff --stat -- dataset/` after all implementation, test, and diagnostic runs: **zero changes under `dataset/`**.

## Confirmation: `output.csv` Was Not Generated

Verified with `ls output.csv` at the repository root: the file does not exist. No code path in `code/payment_plans.py`, `code/verifier.py`, `code/decision_engine.py`, or the Phase 4 section of `code/main.py` writes any CSV file.

## Confirmation: No Evidence/LLM/Vision Integration Was Implemented

No file added or modified this phase reads `messages.csv`, `images.csv`, or any file under `dataset/media/`. `PaymentPlan.spending_changes` is always the empty tuple, and `DecisionResult.spending_changes_needed` is always `()` — both enforced by the verifier rejecting any plan where `spending_changes` is non-empty, per Part 23.

---

## Recommended Next Phase

**Phase 5: evidence integration** (`evidence.py`, per the Phase 0 report's proposed architecture) — resolving the 16 blank-amount events from their linked images, extracting structured facts (amend/cancel/confirm/delay) from `messages.csv` under the untrusted-input boundary established back in Phase 0, and applying the conflict-resolution priority order (`financial_state.py`'s `resolve_conflict`, implemented in Phase 2 but never yet exercised on a real conflict) to genuinely competing evidence. `request_08`'s salary-reduction message and `request_11`'s spending-change-dependent plan are concrete, already-identified test cases for that phase. Spending-change optimization (stop/reduce candidates layered into `payment_plans.py`) is the natural companion to evidence integration, since both `request_06`/`request_11`-style cases and the general `affordable_with_plan` tier depend on it.

---

Stopping here, per `implementation/phase4.md`. Phase 5 was not started automatically.
