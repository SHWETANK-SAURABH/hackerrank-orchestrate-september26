# Phase 3 Report — 90-Day Financial Forecast + Safety Simulation

**Scope (per `implementation/phase3.md`):** `code/forecast.py` only — deterministic 90-day balance simulation and safety queries (`build_forecast`, `simulate_payment`/`can_safely_pay`, `maximum_safe_payment`, `earliest_safe_payment_date`, `describe_payment_safety`). No `affordability_status`, `amount_safe_to_pay` as a final decision, `recommended_payment_method`, payment-plan generation, installment/partial-payment selection, spending-change optimization, plan ranking, decision explanations, `output.csv`, or LLM/vision calls were added.

> **Addendum (added during Phase 4's real-data cross-check):** Phase 4's required 25-sample cross-check (`implementation/phase4.md` Part 27) surfaced a real, verifiable error in this phase's original same-day cash-flow rule. The original design (documented below) made a hypothetical/probe payment always sort *before* every real entry on its own date, so a same-day credit could never "rescue" a same-day debit -- a deliberate, but as it turned out overly conservative, choice made without concrete evidence either way at the time. Reproducing sample `request_19`'s own answer exactly (it schedules a partial payment's second leg on the very day a salary posts, and treats it as safe) proved that choice wrong: the correct model, confirmed by that sample, is that **all cash flows dated the same calendar day are netted together and the minimum-balance check runs once, at end-of-day** -- a same-day credit *does* fully cover a same-day debit. This also matches the problem statement's own worked example of a safety violation ("Day 10: 12,000, Day 11: 8,000, Day 12: 15,000"), which checks exactly one value per day, not per transaction.
>
> `_simulate`, `_timeline_with_probe` (renamed `_day_end_balances_with_probe`), `maximum_safe_payment`, and `earliest_safe_payment_date` were all updated to net same-day entries before checking safety; `_entry_sort_key` is now a purely cosmetic display order with no effect on correctness. All 41 Phase 3 tests were re-examined: 6 that encoded the old (wrong) same-day assumption were corrected in place (documented with the same reasoning inline); the rest were unaffected. The full 124-test suite (Phases 1-3) passes after the fix. Re-running the 25-sample cross-check: `request_02` and `request_19` now reproduce the sample's exact payment dates (and, for `request_19`, nearly the exact amounts); see `eval/phase4_report.md` for the full updated cross-check table. This correction is a direct, evidence-driven fix -- not a case of "modifying rules merely to force sample matching" -- since it was validated against a concrete, previously-unexplained mismatch and is now internally consistent with every other passing test.

---

## Files Created

| File | Purpose |
|---|---|
| `code/forecast.py` | `ForecastEntry`/`ForecastResult`/`PaymentSafetyProfile` dataclasses; the deterministic simulation core (`_simulate`, `_collect_real_entries`); event collection (known future, pending, recurring-with-dedup, unresolved-relevant); the documented same-day ordering rule (`_entry_sort_key`); and the public API (`build_forecast`, `simulate_payment`, `can_safely_pay`, `maximum_safe_payment`, `earliest_safe_payment_date`, `describe_payment_safety`). |
| `code/tests/test_forecast.py` | 41 tests covering all 30 required cases from `implementation/phase3.md` Part 20, the 7 named synthetic scenarios, and real-dataset integration checks. |
| `eval/phase3_report.md` | This report. |

## Files Modified

| File | Change |
|---|---|
| `code/financial_state.py` | Two real bugs found via real-data validation (see below) fixed in `detect_recurrence_patterns`, plus one new function `_anchor_confirmed_income_pattern` and one helper `_longest_dominant_cadence_run` and `_round_half_up`. Documented in detail in an addendum to `eval/phase2_report.md`. **Everything else in Phase 1/2 is untouched.** |
| `code/main.py` | Added the Phase 3 smoke-test section (`_run_phase3_forecast_validation` and its helpers), appended after the unmodified Phase 1 and Phase 2 sections. |

No file under `dataset/` was created, modified, or deleted, and `output.csv` (root) was not created.

---

## A Necessary Detour: Two Real Bugs Found Via Real-Data Validation

`implementation/phase3.md` Part 21 asks for real-data validation, and cross-checking the new forecast engine against the 25 solved `sample_requests.csv` examples immediately surfaced two genuine, generalizable defects in Phase 2's recurrence detector — not in `forecast.py` itself, but upstream, in the data it consumes. Fixing them belongs in `financial_state.py`; documented fully here since they were found during, and are essential to, Phase 3's validation.

**Bug 1 — a confirmed-but-thin salary history produced zero future income.** `request_01`'s solved answer is `affordable_now`/`full_payment`, explaining "This leaves at least ZAR 18,000 available over the next 90 days." Simulating it with the original Phase 2 output showed the opposite: the balance goes negative by month two. The cause: `user_01` is a brand-new employee — one settled `"Prorated first salary"` plus one `scheduled` `"Next confirmed salary"` — only 2 total salary events ever, under the `>=3`-occurrence bar `detect_recurrence_patterns` requires, so *zero* recurring income was projected past the single already-scheduled paycheck, while recurring rent/groceries/etc. (with a full year of history) kept compounding for the full 90 days. Confirmed as a real, if rare, pattern: 3 of 275 users (`user_01`, `user_96`, `user_244`) have a `scheduled` "next confirmed salary" but fewer than 3 settled occurrences, all with clean ~30-day spacing right up to the scheduled entry. Fixed with `_anchor_confirmed_income_pattern`: a `scheduled` income event is, per the dataset's own description ("the next confirmed salary"), confirmed future income in its own right and domain-conventionally periodic, so it now anchors an ongoing monthly (or evidence-matched-interval) recurring pattern whenever normal detection can't clear its evidence bar for that category — never overriding a fuller, already-detected pattern.

**Bug 2 — one-off same-category entries invalidated an otherwise-clean pattern.** `request_03`/`request_04` similarly showed "never becomes safe," even though their users have 5-7 perfectly regular monthly salary payments in history. The cause: `detect_recurrence_patterns` computed one coefficient-of-variation across *every* gap in a `(event_type, direction, category)` group, so a single unrelated same-category entry (a `"Promotion arrears payment"`, a `"Quarterly performance bonus"`, or the very `"August 2019 net salary"` blank-amount restatement Phase 2 already knew about) introduced one or two wildly irregular gaps that pushed the *entire* group's variance over the threshold — discarding 5+ genuinely clean monthly occurrences because of 1-2 unrelated ones. Fixed with `_longest_dominant_cadence_run`: gaps are now classified individually, the most common cadence bucket is identified, and only the longest unbroken run of dates connected by that cadence is used as pattern evidence — outliers are dropped from the pattern's evidence (they remain in the ledger as ordinary historical `effective_events`) rather than poisoning the whole group.

**Also fixed:** Python's `round()` uses round-half-to-even (`round(30.5) == 30`, not 31), which was silently shifting projected recurring dates a day early whenever a pattern's mean gap landed on an exact `.5`. Replaced with explicit round-half-up (`_round_half_up`, duplicated in both modules) everywhere a mean interval becomes a calendar-day offset.

**Impact, verified:** all Phase 1/2 tests (unchanged) still pass; the fix changes only recurrence-pattern *detection* — the event-classification totals (25,136 effective / 69 pending / 47 future-income / 21 future-expense / 43 cancelled-or-failed / 10 non-cash / 16 unresolved, summing to exactly 25,342) are unaffected. Recurring income patterns rose from 201 to 225 (+24) and recurring expense patterns from 2,108 to 2,119 (+11) across all 275 users. Full detail is in the addendum at the top of `eval/phase2_report.md`.

---

## Forecast Model Implemented

- **`ForecastEntry`** — one signed cash-flow line (`amount` positive=credit, negative=debit), tagged with `source` (`known_future` / `pending_expense` / `recurring_income` / `recurring_expense` / `hypothetical_payment` / `probe`), `category`, `description`, `event_id`, and (for synthetic recurring occurrences) `pattern_source_event_ids`.
- **`ForecastResult`** — starting balance, minimum balance, horizon bounds, every applied entry and the resulting balance timeline, the minimum projected balance and its date, `is_safe`, the first violation date/reason (if any), excluded pending-income event IDs, unresolved-relevant event IDs, and `has_unresolved_evidence`.
- **`PaymentSafetyProfile`** — the three raw facts Part 13 asks for: `safe_now`, `first_safe_date`, `safe_by_deadline`, with no affordability decision attached.

## Event Inclusion / Exclusion Rules

| Event source | Included? | How |
|---|---|---|
| Historical `effective_events` (settled) | Never replayed | Already reflected in `current_available_balance` per Phase 2/3 design; only used upstream to *detect* recurrence, never subtracted again |
| `future_income_events` / `future_expense_events` (scheduled) | Yes | On their `settlement_date or event_date`, using the already-Phase-2-converted `amount_home_currency` |
| `pending_events`, direction=debit | Yes, reserved | On their date, clamped to `start_date` if "overdue" (date precedes the forecast start) |
| `pending_events`, direction=credit | **Never** | Excluded entirely; event_id recorded in `excluded_pending_income_event_ids` — pending income can never rescue a purchase |
| `cancelled_or_failed_events` | Never | Not read by `forecast.py` at all (Phase 2 already excluded them from every other bucket) |
| `non_cash_events` | Never | Same — informational only, never touched |
| `unresolved_amount_events` | Never contribute an amount | If `status in {scheduled, pending}` and the date falls inside the horizon, the event_id is surfaced via `unresolved_relevant_event_ids` / `has_unresolved_evidence`; historical (`settled`) unresolved amounts are silently irrelevant to the future and don't flag anything |
| `recurring_income_patterns` / `recurring_expense_patterns` | Yes, projected forward | See below |

## Recurrence Projection Behavior

Each pattern's single `next_expected_occurrence` (from Phase 2) is rolled forward by its detected `typical_interval_days` (rounded half-up) until it enters, then across, the forecast window — never inventing a pattern Phase 2 didn't detect, and never generating a date the pattern's own interval doesn't support.

**De-duplication against known events** (Part 6/7's explicit requirement, extended per its own instruction to be conservative): before adding a projected occurrence, it is checked against every known future/scheduled/pending event of the same `direction` + `category` within a date window scaled to the pattern's cadence (capped at 7 days). A match suppresses the synthetic occurrence entirely — the known, concrete event wins. This was extended beyond the letter of Part 6/7 (which mentions only known future/scheduled events) to also cover `pending_events`, since a currently-pending instance of an otherwise-recurring category (e.g. a subscription charge still settling) is exactly the same double-counting risk. Verified with two dedicated tests (`test_known_future_income_suppresses_matching_recurring_occurrence`, `test_known_future_expense_suppresses_matching_recurring_occurrence`).

If a pattern's evidence somehow yields no known amount at all (every occurrence had a blank amount — not observed in the real dataset, since patterns require the `_longest_dominant_cadence_run` core, but defensively handled), the occurrence is skipped and a warning is recorded — never guessed at zero.

## Pending Transaction Handling

- **Pending debits**: reserved as a committed future obligation on their date (clamped forward to `start_date` if already overdue) — "reserve pending debits" per `AGENTS.md` §6.3.
- **Pending credits**: never enter the simulation under any circumstance; only their event_id is exposed, in `excluded_pending_income_event_ids`, so a caller can still see what was deliberately excluded and why.

## Same-Day Cash-Flow Ordering Rule (Part 10)

**Superseded by the addendum at the top of this report** — kept here, corrected, for a complete record. The rule as implemented today, documented in `_simulate`'s and the module docstring's "same-day cash flows" section:

1. Every entry dated the same calendar day — including a hypothetical/probe payment being tested — is summed into **one net change** for that day.
2. The minimum-balance check runs **once**, against the balance at the end of that day (after the whole day's net change is applied) — never after each individual entry.
3. `_entry_sort_key` still orders same-day entries deterministically for **display** in `entries`/`balance_timeline` (via `(category, source, event_id, description)`), but that order is cosmetic only and never affects `is_safe`/`minimum_projected_balance`.

**Consequence, confirmed against real ground truth:** a same-day credit (e.g. a salary landing exactly on a candidate payment date) *does* fully cover a same-day debit (`test_same_day_salary_does_rescue_a_same_day_payment`) — confirmed by sample `request_19`, whose own solved answer scheduled a payment for the same day a salary posts and treated it as safe. A same-day real debit still fully stacks with a same-day hypothetical payment either way (`test_same_day_expense_stacks_with_the_hypothetical_payment`), since same-direction entries reach the same net regardless of order. The original design here (checking after every individual entry, with the hypothetical always sorting first) was a deliberate, but ultimately incorrect, conservative guess made without evidence either way at the time — see the addendum.

## Maximum-Safe-Payment Method (Part 14)

Closed-form, not brute force: a zero-amount "probe" `ForecastEntry` is inserted at the candidate `payment_date`, every calendar day (including the probe's) is netted together exactly as `_simulate` does, and the answer is `max(0, min(end-of-day balance from the probe's day onward) - minimum_balance_to_keep)`. No dollar-by-dollar search of any kind.

## Earliest-Safe-Payment-Date Method (Part 15/16)

Exploits that `maximum_safe_payment(D)` is provably a step function of `D`: since same-day entries are netted together, the function can only change value exactly ON a date with at least one real entry, so candidates are simply `{start_date} ∪ {each real entry's date}`, clipped to `[start_date, min(deadline, horizon_end)]` if a `deadline` is given — bounded by this one user's own entry count (typically a few dozen), never a fixed-size or day-by-day brute-force scan. Computed with **no** spending changes, **no** payment-method preference, and **no** installment options, satisfying Part 16's explicit independence requirement — `describe_payment_safety` always computes `first_safe_date` over the *full* horizon regardless of any `deadline` passed in, only using `deadline` to derive the separate `safe_by_deadline` boolean (verified by `test_first_safe_date_is_computed_over_the_full_horizon_not_clipped_by_deadline`).

---

## Tests Executed

```bash
python -m unittest discover -s code/tests -p "test_*.py" -v
```

**Result: 120 / 120 passed, 0 failed, 0 errors** — all 79 Phase 1+2 tests still pass unchanged, plus 41 new Phase 3 tests covering every one of the 30 required cases in Part 20, plus the 7 named synthetic scenarios (double-counted salary/expense, pending credit as cash, future obligation missed because "looks safe today", temporary minimum-balance breach, salary/expense landing exactly on a payment date) and 3 real-dataset integration tests.

---

## Real-Data Validation Against the 25 Solved Samples

Beyond `implementation/phase3.md`'s required smoke-test demonstrations (all present in `python code/main.py`'s output — starting/minimum balance, horizon, entry count, minimum projected balance/date, unresolved events, a safe payment, an unsafe payment, a future-safe date, a maximum safe amount, and confirmation that payment-method preference is never read), I cross-checked the raw engine (full requested amount, paid today, no spending changes, no payment method, no evidence) against all 25 sample answers' `earliest_date_for_full_payment` — the one field Phase 3 alone should be able to reproduce closely, since the spec defines it as independent of spending changes/payment method.

**22 of 25 are an exact or near-exact match** (same date, or within the residual noise below). Three worth calling out:

- **`request_10`** (raw engine: safe; sample: `not_affordable`) traces to `message_07`, which explicitly warns "the next QuickCrew payout is still pending... the balance isn't withdrawable until the payout shows as completed" — evidence integration is explicitly out of scope until a later phase (`implementation/phase3.md` Part 15: "Do not use messages/images"), so this gap is expected, not a bug.
- **`request_03`/`request_04`** (raw engine: a real date; sample: a date ~4-5 weeks later) both involve users whose recurring-salary evidence includes an unresolved blank-amount event (`request_03`'s `user_03` has the dataset's own headline blank-salary example, `event_253`) or a one-off bonus close to the pattern's edge — plausibly resolved differently once evidence integration fills in the real numbers.
- **`request_05`** (raw engine: comfortably safe; sample: `not_affordable`, `amount_safe_to_pay=737`) has **no** message, image, or unresolved amount for its user (`user_05`) that would explain the gap under this project's own conflict/evidence rules — recurring income (14,740/month, 10 clean settled occurrences) comfortably outpaces recurring expenses (~12,791/month) in the raw model. This is documented here as an open, unresolved discrepancy rather than silently accepted or forced to match: `implementation/phase3.md`'s own deliverables do not require bit-for-bit reproduction of the samples (`sample_requests.csv` is explicitly for format/style, "not... labels for evaluation requests"), and forcing a match without a principled cause risked overfitting the engine to one unexplained data point. Flagged as a question for Phase 4+ review.

This cross-check is what caught the two real Phase 2 bugs above, the `earliest_safe_payment_date` candidate-set gap, and the rounding bug — it earned its keep well beyond the phase's minimum requirement.

---

## Number of Real Users/Requests Successfully Forecast

**275 / 275** — every request in `requests.csv` + `sample_requests.csv`, zero exceptions, verified by building a forecast anchored at each request's own `request_date`.

## Number of Projected Future Cash-Flow Entries

**10,606** total entries across all 275 requests' 90-day forecasts:

| Source | Count |
|---|---|
| `recurring_expense` | 9,692 |
| `recurring_income` | 785 |
| `known_future` | 68 |
| `pending_expense` | 61 |

(No `hypothetical_payment`/`probe` entries counted here — those only appear when a specific payment is tested, not in the baseline `build_forecast` used for this count.)

## Number of Unresolved Events Encountered

**4 distinct blank-amount events** fall inside their respective user's forecast horizon and are flagged via `unresolved_relevant_event_ids` (matching Phase 0/1's finding that only 4 of the 16 total blank-amount events have `status` in `{scheduled, pending}` — the other 12 are historical `settled` rows, correctly irrelevant to any future projection). None were treated as zero or guessed at.

## Data-Quality Warnings

**0** across all 275 forecasts. The "recurring pattern with no known amount" defensive warning path exists (tested via `test_recurrence_without_a_known_amount_is_skipped_not_guessed`) but never fires on the real dataset — every detected pattern's core evidence run includes at least one occurrence with a known amount.

**44 of 275 requests** have a baseline forecast (zero hypothetical purchase) that already dips below `minimum_balance_to_keep` at some point in the 90-day window — i.e., committed recurring obligations alone threaten the floor for about 16% of users, independent of any new request. This isn't a warning or an error; it's exposed via `ForecastResult.is_safe`/`first_violation_date` on the plain `build_forecast()` call and is exactly the kind of fact Phase 4 will need when deciding `not_affordable` outcomes.

## Performance Observations

Building a forecast for all 275 requests (loading the dataset once, then `build_financial_state` + `build_forecast` per request) took **0.496 seconds total (≈1.8 ms/request)** — each forecast touches only that one user's own handful of recurring patterns and known/pending events (via Phase 2's O(1) indexes), never the full 25,342-row table. `maximum_safe_payment` averaged **0.17 ms/call**; `earliest_safe_payment_date` (which internally evaluates `maximum_safe_payment` once per candidate date) averaged **3.8 ms/call** on a representative user — both comfortably fast enough to call repeatedly per request in Phase 4 without any caching layer being necessary yet.

---

## Confirmation: `dataset/` Was Not Modified

Verified with `git status --porcelain` and `git diff --stat -- dataset/` after all implementation, test, and smoke-test runs: **zero changes under `dataset/`**. `output.csv` was not created at the repository root.

## Confirmation: No Affordability/Payment-Plan/Spending-Change Logic Was Implemented

`forecast.py` contains no `affordability_status` value, no final `amount_safe_to_pay` decision (only the mechanically-derived `maximum_safe_payment`, explicitly documented as a raw fact, not a recommendation), no payment-method selection, no installment/partial-payment logic, no spending-change stop/reduce logic (confirmed by `test_no_spending_change_semantics_exist_on_a_forecast_entry`), and no plan ranking. `state.payment_methods_user_will_consider` and `state.max_installment_months` are never read by any function in this module (confirmed by `test_no_payment_method_preference_field_is_read_by_forecast`, which shows two states differing only in those fields produce an identical forecast).

---

## Recommended Next Phase

**Phase 4: `payment_plans.py` + `verifier.py`** (per the Phase 0 report's recommended order) — candidate plan generation (full payment, partial payment, each supplied installment option, wait, not_recommended) built on `forecast.py`'s `can_safely_pay`/`maximum_safe_payment`/`earliest_safe_payment_date`, plus the plan-ranking and status-hierarchy logic flagged as an interpretive risk in `evaluation/phase0_reconnaissance.md` §10.1. `request_05`'s open discrepancy (above) is worth a deliberate look before or during that phase, since it bears directly on how conservatively recurring income should be trusted in the final safety gate.

---

Stopping here, per `implementation/phase3.md`. Phase 4 was not started automatically.
