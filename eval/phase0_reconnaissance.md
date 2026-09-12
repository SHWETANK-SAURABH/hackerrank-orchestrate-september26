# Phase 0 — Repository & Dataset Reconnaissance

**Challenge:** HackerRank Orchestrate (September 2026) — *Buy or Wait?*
**Scope:** Read-only inspection. No decision logic, LLM calls, or dataset/output changes were made in this phase.

---

## 1. Repository Structure

```text
hackerrank-orchestrate-september26/
├── AGENTS.md                 # Agent rules + logging contract (source of truth)
├── CLAUDE.md                 # @AGENTS.md import for Claude Code
├── README.md                 # Participant-facing quick start + summary spec
├── problem_statement.md      # Full task spec (authoritative for output rules)
├── .venv/                    # Local Python 3.11.15 virtualenv (no packages installed yet — no pandas)
├── code/
│   ├── main.py                    # EMPTY (0 bytes) — intended entry point (`python3 code/main.py`)
│   └── evaluation/
│       ├── main.py                # EMPTY (0 bytes)
│       └── usage_report.md        # EMPTY (0 bytes) — required deliverable per §6.5/README "Token Usage"
└── dataset/
    ├── requests.csv                  # 250 rows — THE evaluation set (predict these)
    ├── sample_requests.csv           # 25 rows — solved examples (format/style reference only)
    ├── financial_profiles.csv        # 275 rows — one per user (25 sample users + 250 eval users)
    ├── financial_events.csv          # 25,342 rows — historical/pending/scheduled ledger (2.9 MB)
    ├── exchange_rates.csv            # 134 rows — fixed dated FX rates
    ├── request_payment_options.csv   # 790 rows — 2-4 options per request
    ├── messages.csv                  # 215 rows — free-text evidence, multilingual
    ├── images.csv                    # 16 rows — links image_id → user/request/event
    ├── output.csv                    # 250 rows — BLANK template (all prediction columns empty)
    └── media/images/                 # 16 valid PNGs (image_01.png … image_16.png), 32 KB–1.1 MB each
```

Newly created this phase (both required by AGENTS.md §2, not part of the submission dataset):
- `.gitignore` (added `log.txt`)
- `log.txt` (session log, git-ignored)
- `evaluation/phase0_reconnaissance.md` (this file)

**No file under `dataset/` was modified.** `code/main.py`, `code/evaluation/main.py`, and `code/evaluation/usage_report.md` are empty stubs — implementation has not started.

**Entry point confirmed:** README says "Build your solution in `code/main.py`" and run it with `python3 code/main.py`; it must read from `dataset/` and write `output.csv` to the **repository root** (not `dataset/output.csv`, which is only the blank reference template). Python 3.11.15 is available via `python`; `pandas` is **not** installed in `.venv` — either add it or use the stdlib `csv` module (this recon used stdlib `csv` only).

---

## 2. Specification Summary (README.md + problem_statement.md + AGENTS.md §6)

### Required output — `output.csv`
Exact column order (both root and `dataset/` copies must match):
```
request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation
```
One row per row in `dataset/requests.csv` (250 data rows + header). `0 <= amount_safe_to_pay <= requested_amount` always.

### Allowed enum values
- `affordability_status`: `affordable_now` | `affordable_with_plan` | `affordable_later` | `not_affordable`
- `recommended_payment_method`: `full_payment` | `partial_payment` | `installments` | `wait` | `not_recommended`

### Status semantics (important — see §13 ambiguity note)
- `affordable_now`: full amount safe **on `request_date`**, and user accepts `full_payment`. Requires `earliest_date_for_full_payment == request_date`.
- `affordable_with_plan`: full request completed via **partial-payment schedule, installments, or permitted spending changes** (not merely "waiting").
- `affordable_later`: full amount becomes safe **later, on its own** (no plan/changes) → paired with `wait`.
- `not_affordable`: cannot be completed safely within the forecast window at all.

### `payment_plan` rules
- Chronological `YYYY-MM-DD:amount` entries joined by `|`, or literal `none`.
- **Partial payment**: exactly two entries — `amount_safe_to_pay` on `request_date`, then `requested_amount - amount_safe_to_pay` on `earliest_date_for_full_payment`. Must sum to `requested_amount`. Only valid when: request allows it (`allows_partial_payment=true`), user's `payment_methods_user_will_consider` includes `partial_payment`, `0 < amount_safe_to_pay < requested_amount`, and `earliest_date_for_full_payment <= desired_completion_date`. Does **not** need to match a supplied payment option.
- **Installments**: must exactly match one row of `request_payment_options.csv` for that request (dates/amounts/count all follow the option). An available option can still be rejected for conflicting with `payment_methods_user_will_consider` or `max_installment_months`.

### `earliest_date_for_full_payment`
- Computed **without** optional spending changes and **independent of the user's method preference** (per spec: "measures financial capacity independently of the user's payment-method preferences"). Equals `request_date` for `affordable_now`; empty if never safe within the forecast.
- This is a distinct, "natural" measure from whatever is actually recommended — a plan can recommend paying today via a spending change even though `earliest_date_for_full_payment` (no-changes basis) is later. See sample `request_06` in §8.

### `spending_changes_needed`
- Up to three of `stop:<event_id>` / `reduce_to:<event_id>:<new_amount>`, `|`-joined, or `none`.
- Stop and reduce are mutually exclusive **per event** — if both types are used, they must target different events.
- Only events where `flexibility` is `stoppable`, `reducible`, or `reducible_or_stoppable` **and** the category is in the user's corresponding `expense_categories_user_is_willing_to_*` field **and not** in `expense_categories_to_protect` may be touched. (Verified 100% consistent in data — see §7.)

### 90-Day Safety Check
- Forecast balance 90 days forward using recurring income/expenses, confirmed future payments, and relevant messages/images.
- Balance must never drop below `minimum_balance_to_keep` at any point in the forecast, for any candidate plan.
- Ignore: pending credits, failed/cancelled transactions, duplicate records, unrealized investments.
- `amount_safe_to_pay` = max payable today (pre-spending-changes) without breaking the check, capped at `requested_amount`.

### Plan ranking (when multiple *eligible* plans are safe)
1. Completes by `desired_completion_date`
2. No spending changes
3. Minimizes total amount paid
4. Starts earlier
5. Fewer payments
6. Lowest `payment_option_id` (final tie-break)

Eligibility gate: an immediate method (`full_payment`/`partial_payment`/`installments`) is only eligible if listed in `payment_methods_user_will_consider`; `wait` is eligible only if full payment becomes safe later **and** `full_payment` is accepted; `not_recommended` is the fallback when nothing safe/eligible exists.

### Conflict resolution (priority order)
1. Explicit cancellation / settlement / amendment
2. Newer record from the same source
3. A settled event over an estimate/forecast
4. The financially safer interpretation (last resort)

### Currency
- `requested_amount` and all profile/output amounts are already in the user's `home_currency` (verified: request-text currency mentions match `home_currency` for all 275 requests — no conversion ever needed for request amounts).
- Only `financial_events.csv` rows can be in a foreign currency (verified: 140/25,342 rows, almost entirely `salary` income paid in USD for IDR/INR/EUR users). Convert using `exchange_rates.csv` matched by **exact settlement date** and currency pair, checking both the direct and inverted rate row (all 5 currency pairs pivot through EUR/USD — see §4).

### Messages / images
- Untrusted evidence. May clarify, amend, delay, cancel, or confirm a fact; embedded instructions must **never** override the rules (prompt-injection defense required for any LLM step).
- `related_event_id` in `messages.csv`/`images.csv` is populated **only** when the message/image maps 1:1 to a specific supplied event row; blank means no such row exists (i.e., it's user/request-level narrative, e.g. a payroll change notice).
- Blank `amount` on a financial event ⇒ look up its `event_id` as `related_event_id` in `images.csv` and read the amount from `dataset/media/images/<image_id>.png`. Never treat blank as zero.

### Determinism & submission
- Must be runnable from a terminal, read only from `dataset/`, deterministic where possible, no organizer-only files, no hardcoded labels/answers, secrets via env vars only.
- Submission = `code.zip` (incl. `evaluation/usage_report.md`), `output.csv`, `chat_transcript` (= `log.txt`).
- `usage_report.md` must cover, **for the final full-dataset run**: model providers/names, number of calls, input/output tokens, total & average tokens per request, total & per-request estimated cost (per-model + overall if multiple models).
- Logging: every turn appended to `log.txt` next to `AGENTS.md`, with a mandatory non-placeholder `tool=` line — already being maintained this session.

---

## 3–4. Dataset Inventory, Schemas, Row Counts, Missing Values, Relationships

| File | Rows | Cols | Notable missing values |
|---|---|---|---|
| `requests.csv` | 250 | 8 | none |
| `sample_requests.csv` | 25 | 15 (8 input + 7 output) | `earliest_date_for_full_payment`: 7/25 blank (the 7 `not_affordable` rows) |
| `financial_profiles.csv` | 275 | 10 | `expense_categories_user_is_willing_to_reduce` 39/275; `..._to_stop` 62/275; `max_installment_months` 119/275 (blank = user won't consider installments) |
| `financial_events.csv` | 25,342 | 14 | `amount` 16/25342 (⇒ always resolved via an image, see §6); `settlement_date` 10/25342; `linked_event_id` 25,284/25342 (only 58 populated); `minimum_allowed_amount` 22,435/25342 (populated only when `flexibility` ∈ {reducible, reducible_or_stoppable}) |
| `exchange_rates.csv` | 134 | 4 | none |
| `request_payment_options.csv` | 790 | 9 | `payment_frequency_days` 275/790 (blank for the 275 `full_payment` options, i.e. single payment) |
| `messages.csv` | 215 | 7 | `request_id` 87/215; `related_event_id` 176/215 |
| `images.csv` | 16 | 4 | none |
| `output.csv` | 250 | 8 | all 7 prediction columns 100% blank (template) |

### Columns
- **requests.csv**: `request_id, user_id, request_date, request_type, requested_amount, desired_completion_date, allows_partial_payment, request_text`. `request_type` is one of 9 values, evenly distributed (~28 each). `allows_partial_payment`: 170 false / 80 true.
- **financial_profiles.csv**: `user_id, home_currency, current_available_balance, minimum_balance_to_keep, financial_priorities, expense_categories_to_protect, expense_categories_user_is_willing_to_reduce, expense_categories_user_is_willing_to_stop, payment_methods_user_will_consider, max_installment_months`. `home_currency` ∈ {ZAR, IDR, EUR, INR, USD}, fairly balanced (40–67 users each).
- **financial_events.csv**: `event_id, user_id, event_type, description, category, direction, amount, currency, event_date, settlement_date, status, linked_event_id, flexibility, minimum_allowed_amount`.
  - `event_type` ∈ {expense (20,525), subscription (2,488), income (1,696), debt_payment (567), investment_purchase (29), refund (22), investment_sale (5), investment_valuation (10)}.
  - `direction` ∈ {debit (23,609), credit (1,723), non_cash (10 — all `investment_valuation`)}.
  - `status` ∈ {settled (25,148), pending (71), scheduled (70), cancelled (22), failed (21), unrealized (10 — all `investment_valuation`)}.
  - `flexibility` ∈ {fixed (21,138), reducible (2,682), stoppable (1,297), reducible_or_stoppable (225)}. **No `expense` rows are `stoppable`** — only `subscription`-type rows can be fully stopped; expenses can only be `reducible`. This maps cleanly onto the profile's `expense_categories_user_is_willing_to_stop` (subscription-type categories: streaming, cloud_storage, music_subscription, delivery_membership, gym) vs. `..._to_reduce` (variable spend categories: dining, shopping, streaming, entertainment, gym).
  - `category` universe (22 values) is a strict superset of the categories referenced anywhere in `financial_profiles.csv`'s protect/reduce/stop fields; the extra categories (`investment`, `salary`, `windfall`, `work_expense`) never appear as flexible (always `fixed`) — consistent with them being income/investment/one-off types, not adjustable spend.
- **exchange_rates.csv**: `rate_date, from_currency, to_currency, rate`. `from_currency` is only ever `EUR` or `USD` (i.e. USD/EUR act as pivots); `to_currency` covers all 5 currencies. 39 distinct `rate_date`s, monthly (typically the 15th; one exception `2025-10-01`), spanning 2023-10-15 → 2026-11-15.
- **request_payment_options.csv**: `payment_option_id, request_id, payment_method, payment_amount, number_of_payments, first_payment_date, payment_frequency_days, financing_fee, total_payable_amount`. Verified `payment_amount * number_of_payments == total_payable_amount` (fee already baked into `payment_amount`) for every sampled row. `payment_method` ∈ {full_payment (275, one per request), installments (515)}. Options per request: 2 (65 requests), 3 (180 requests), 4 (30 requests) — matches spec's "two to four options."
- **messages.csv**: `message_id, user_id, request_id, related_event_id, sent_at, source_type, message_text`. `source_type` ∈ {employer (126), financial_service (23), bank (18), merchant (17), service_provider (31)}. Message text is **multilingual** — e.g. Indonesian for several IDR-currency users' employer messages — a real localization/parsing consideration if any LLM step reads these directly.
- **images.csv**: `image_id, user_id, request_id, related_event_id` — exactly 16 rows, 1:1 with the 16 PNG files and the 16 events that have a blank `amount`. Every blank-amount event in this dataset **does** have a matching image (no unresolved gaps).

### Key relationships (confirmed by direct inspection, not assumed)
- **`request_id` ↔ `user_id` is a strict 1:1, same-index mapping**: `request_N` always belongs to `user_N`, for all 275 requests (25 samples + 250 eval) and all 275 profiles. No user has more than one request; no profile is request-less.
- `financial_events.request_id` — **there is no such column**; events join to a user only via `user_id`, and to a request indirectly (a request's relevant events are simply "this user's events," filtered by date/category/relevance — there is no explicit event↔request FK).
- `request_payment_options.request_id` → `requests.request_id` / `sample_requests.request_id` (every request has 2–4 rows).
- `messages.request_id` (nullable) and `messages.related_event_id` (nullable) → both can be blank simultaneously (76/215 messages — pure user-level narrative, e.g. payroll change notices with no linked request or event), request-only (100), event-only (11), or both (28).
- `images.related_event_id` → `financial_events.event_id`, always populated (16/16), always exactly the events with blank `amount`.
- `exchange_rates` join key is **(rate_date, from_currency, to_currency)** — must also check the **inverted** pair (e.g. need `IDR→USD`, table only has `USD→IDR`) since `from_currency` is restricted to {EUR, USD}. All 140 foreign-currency events resolved with an **exact-date, single-hop (direct-or-inverted)** lookup in this dataset — no case required USD→EUR→IDR-style double conversion, but code should not assume that will always hold for other data.
- `financial_events.linked_event_id` → an earlier `event_id` for the *same user*. Only 58/25,342 rows populated. Observed patterns: refund → original expense (`event_99`→`event_98`), a cancelled card authorization → the actual settled charge that replaced it (`event_101`→`event_100`, where `event_100` itself is `status=cancelled`), and investment lifecycle links (`investment_valuation`→`investment_purchase`). **The link alone does not determine cash-flow treatment** (per spec) — the row's own `status`/`direction` still governs whether it counts.

---

## 5. sample_requests.csv — Traced Examples

All 25 samples were loaded and cross-referenced against `financial_profiles.csv`, `financial_events.csv`, `request_payment_options.csv`, `messages.csv`, and `images.csv`. Representative traces:

**request_01 (user_01, ZAR, purchase, affordable_now/full_payment)** — `amount_safe_to_pay = requested_amount = 25256`, plan is a single payment on `request_date`. Explanation cites the resulting balance vs. the ZAR 18,000 minimum from `financial_profiles.csv`.

**request_02 (user_02, IDR, travel, affordable_with_plan/installments)** — `amount_safe_to_pay = 17,229,139.2` (< requested 46,018,000). Plan is 3 payments of 15,952,906.67 starting 2025-08-08 — this **exactly matches** a row in `request_payment_options.csv` (payment_option with `number_of_payments=3`, `payment_amount≈15,952,906.67`). `earliest_date_for_full_payment = 2025-09-15` (later than the plan's completion on 2025-10-07's *last* installment — note this date is about when the *unassisted* full amount would be safe, not tied to the installment schedule).

**request_03 (user_03, IDR, education, affordable_later/wait)** — `amount_safe_to_pay = 873,000` (well under requested 5,491,000). `payment_plan = 2019-11-15:5491000` (single full payment on the deadline, not on `request_date`). Explanation: paying earlier breaches the IDR 2,668,700 minimum.

**request_05 (user_05, ZAR, debt_repayment, not_affordable/not_recommended)** — `amount_safe_to_pay = 737` only; `payment_plan = none`; `earliest_date_for_full_payment` blank. Explanation states no available option keeps the ZAR 13,100 minimum protected within the deadline.

**request_06 (user_06, EUR, investment, affordable_with_plan/full_payment, spending change)** — `requested_amount = 620.40`; unassisted `amount_safe_to_pay = 603.30` (shortfall 17.10); `spending_changes_needed = stop:event_476` (a EUR 19/month "Family streaming plan" subscription, `flexibility=stoppable`, category `streaming`, present in `user_06`'s `expense_categories_user_is_willing_to_stop`). Stopping it frees enough to pay the **full** amount on `request_date` itself. Note `earliest_date_for_full_payment = 2026-01-15` (12 days *after* `request_date`) — this is the *unassisted* date, deliberately later than the actual (assisted) payment date in the plan. **This is the clearest evidence that `earliest_date_for_full_payment` is computed independently of any spending-change plan actually recommended** (see §13).

**request_19 (user_19, INR, purchase, affordable_with_plan/partial_payment)** — `requested_amount = 39,660`; `amount_safe_to_pay = 28,820`; plan = `2024-09-04:28820|2024-09-15:10840`, summing exactly to 39,660, second payment on/before `desired_completion_date`.

Full enumeration of the 25 samples' `affordability_status` × `recommended_payment_method` pairs: `affordable_now/full_payment` (3), `affordable_with_plan/installments` (5), `affordable_with_plan/full_payment` (1, via spending change), `affordable_with_plan/partial_payment` (1), `affordable_later/wait` (6), `not_affordable/not_recommended` (7 — the fallback dominates the unsafe cases), plus remaining combinations rounding out 25.

No sample values were used to derive rules beyond what the written spec already states — patterns above corroborate the spec, they don't add unwritten rules, **except** the `earliest_date_for_full_payment` independence behavior in `request_06`, which is implied by spec wording but only becomes unambiguous by seeing this example.

---

## 6. Important Edge Cases (with concrete dataset evidence)

| Edge case | Evidence |
|---|---|
| Blank amount requiring image lookup | 16 events (`event_253`, `event_1442`, …) all blank `amount`, all present in `images.csv`, all resolvable to `dataset/media/images/image_XX.png` |
| Recurring vs. one-time | `subscription` (2,488 rows, always `settled`, monthly cadence, e.g. streaming/cloud_storage/gym) vs. one-off `expense`/`refund`/`investment_*` rows — must be detected from history, not a flag |
| Pending transactions | 71 `pending` events (mostly `expense`, some `refund`) — must be reserved (debits) but pending *credits* excluded from available cash per spec |
| Cancelled / failed | 22 cancelled, 21 failed — excluded entirely from cash-flow forecast |
| Confirmed future income | 47 `income` rows with `status=scheduled` — exactly **one per user** (of the 47 users who have one) — "next confirmed salary," counted on its settlement date only |
| Foreign currency income | 140 events (139 `salary`, 1 stray `transport` expense) where event `currency` ≠ user `home_currency` — almost entirely USD-paid salaries for IDR/INR/EUR-home users — needs `exchange_rates.csv` conversion with a direct-or-inverted lookup |
| Multiple payment options | 2–4 rows per request in `request_payment_options.csv`; `financing_fee` baked into `payment_amount`; `total_payable_amount = payment_amount * number_of_payments` always |
| Installments vs. `max_installment_months` | 119/275 users have blank `max_installment_months` (won't consider installments at all); for users with a cap set, a naive month-count check found **193** installment options whose duration would exceed the cap — i.e., many "available" options must be rejected per-user, this is not a rare corner case |
| Reject specific payment methods | `payment_methods_user_will_consider` has 7 distinct combinations (e.g. `full_payment` only: 60 users; `installments` only: 41 users) — an "available" option can still be inadmissible for a given user |
| Flexible vs. protected categories | Verified 100% consistency (4,204 non-fixed events checked, 0 mismatches) between an event's `flexibility` value and the *specific user's* willing-to-reduce/stop/protect fields — the event-level flag and the profile-level policy always agree in this dataset, but both must be checked in code since they are logically independent fields |
| Linked events / dedup | 58 `linked_event_id` rows: refund→original-expense pairs, a cancelled-authorization→settled-charge pair, and investment-lifecycle chains (`investment_purchase`→`investment_valuation`→`investment_sale`) |
| Investment requests | `investment_purchase` (29, settled), `investment_valuation` (10, all `unrealized`/`non_cash` — must be excluded from cash), `investment_sale` (5, settled) |
| Windfall / bonus income | 6 `windfall`-category credit rows ("Prize proceeds"), always `settled` in this data but conceptually the kind of pending/one-off credit the spec says not to count until settled |
| Messages amending financial facts | 215 messages, multilingual (English + Bahasa Indonesia observed), various `source_type`s (employer, bank, merchant, service_provider, financial_service); 28 tie to both a request and a specific event, 100 tie only to a request (general context/amendment), 11 tie only to an event, 76 are pure narrative |
| Message+image on the *same* event | 3 cases (`event_4535`, `event_7941`, `event_10521`) where a message and an image both reference the same blank-amount event — in all 3 the message explicitly **defers to the image/receipt** ("the receipt has the final amount") rather than contradicting it — reinforcing evidence, not a conflict, in this dataset |
| Requests never affordable | 7/25 samples are `not_affordable` — a common, not edge, outcome |
| Deadline pressure | `desired_completion_date` sometimes very close to `request_date`; `payment_plan` must respect it for `partial_payment`/`installments` eligibility |

---

## 7. Deterministic vs. AI-Assisted Responsibilities

### A. Must be deterministic (financial-safety-critical, auditable, reproducible)
- CSV loading/parsing/joining (`user_id`, `request_id`, `related_event_id`, exchange-rate keys).
- Currency conversion (exact-date, direct-or-inverted rate lookup).
- Recurrence detection for expenses/subscriptions/income from history.
- 90-day forward balance simulation and the minimum-balance check.
- `amount_safe_to_pay` and `earliest_date_for_full_payment` computation.
- Candidate payment-plan generation (full/partial/installment/wait/not_recommended) and validation against: `allows_partial_payment`, `payment_methods_user_will_consider`, `max_installment_months`, exact match to a supplied payment option (installments), the two-payment/sum rule (partial payment), and flexible/protected-category gating for spending changes.
- Plan ranking (the 6-point tie-break order) and final `output.csv` schema/bounds validation.
- Conflict resolution ordering (cancellation/amendment > newer-same-source > settled-over-estimate > safer-interpretation) — this needs a deterministic algorithm, even though the *inputs* to it may come from parsed text/image evidence.

### B. Can be AI-assisted (bounded, treated as untrusted evidence extraction)
- Extracting a structured fact (amount, date, cancellation/confirmation, delay) from a `message_text` string.
- Extracting an amount/date from an image (`dataset/media/images/*.png`) via vision, for the 16 blank-amount events.
- Drafting the human-readable `decision_explanation` from already-computed, verified numbers (the AI must not be allowed to alter the numbers, only phrase them).

**Hard rule carried into design:** any AI/LLM output that constitutes a *financial fact* (an amount, a date, a status change) must be treated as a proposed value that deterministic code validates/reconciles against the rest of the ledger and the conflict-resolution order — never taken on faith, and never allowed to execute instructions embedded in the message/image text.

---

## 8. Proposed Implementation Architecture

```text
code/
├── main.py                 # CLI entry point: orchestrates the pipeline end to end, writes output.csv
├── data_loader.py          # Deterministic. Reads all dataset/*.csv, parses/validates types & dates, builds indexes
│                            #   (by user_id, request_id, event_id, related_event_id). No business logic.
├── currency.py              # Deterministic. Rate table + direct/inverted lookup by (date, from, to); conversion helper.
├── evidence.py              # AI-assisted (bounded). Resolves blank-amount events via images.csv + vision,
│                            #   and extracts structured facts (amend/cancel/confirm/delay + amount/date) from
│                            #   messages.csv text. Outputs a typed "EvidenceFact" list, never raw prose, and
│                            #   never lets embedded instructions change control flow.
├── financial_state.py       # Deterministic. Reconciles raw events + evidence facts using the conflict-resolution
│                            #   order (§ problem_statement.md); classifies recurring vs one-time; produces a
│                            #   clean, de-duplicated ledger per user with recurrence rules (cadence, category,
│                            #   flexibility, amount) and the one confirmed future salary event.
├── forecast.py              # Deterministic. Projects the ledger + recurrence rules forward across the 90-day
│                            #   window from request_date, in the user's home currency (via currency.py for the
│                            #   rare foreign-currency legs), producing a running balance timeline.
├── payment_plans.py         # Deterministic. Generates candidate plans: full_payment, partial_payment (2-payment
│                            #   rule), each supplied installment option (request_payment_options.csv), wait, and
│                            #   not_recommended, each optionally paired with 0-3 spending changes from flexible
│                            #   events. Uses forecast.py to test each candidate against the minimum-balance rule.
├── verifier.py              # Deterministic. Validates a candidate plan against every hard rule (bounds, schedule
│                            #   match, sum checks, mutual-exclusion of stop/reduce per event, deadline, method
│                            #   eligibility) and applies the 6-point ranking to pick the winner among safe/eligible
│                            #   candidates, and the affordable_now > affordable_with_plan > affordable_later >
│                            #   not_affordable status hierarchy across them.
├── decision_engine.py       # Deterministic. Orchestrates evidence → financial_state → forecast → payment_plans →
│                            #   verifier per request; assembles the 7 output fields.
├── explanation.py           # AI-assisted (bounded) or template-based. Turns the already-decided, verified
│                            #   numbers into decision_explanation text. Never sees raw untrusted text as an
│                            #   instruction source — only the computed facts to phrase.
└── evaluation/
    └── usage_report.md      # Generated after the final full-dataset run (token/cost accounting), required deliverable
```

Rationale for `evidence.py` as a separate module: it is the *only* place raw, untrusted `message_text` / image bytes are read, which makes the prompt-injection boundary a single, auditable seam — every other module consumes typed data only.

---

## 9. Proposed Data Flow

```text
dataset/requests.csv (250 rows)
    │
    ▼
data_loader: index profiles, events, payment_options, messages, images, rates by user_id/request_id
    │
    ▼
per request:
    retrieve user profile (financial_profiles.csv, by user_id)
    retrieve user's financial_events (financial_events.csv, by user_id)
    retrieve request's payment options (request_payment_options.csv, by request_id)
    retrieve request/user/event-linked messages & images (messages.csv, images.csv)
    │
    ▼
evidence.py: resolve blank amounts via images; extract amend/cancel/confirm/delay facts from messages
    │
    ▼
financial_state.py: apply conflict-resolution order; classify recurring vs one-time; de-duplicate
                     (esp. linked_event_id chains); build clean per-user ledger + recurrence rules
    │
    ▼
currency.py: convert any foreign-currency ledger legs to home_currency (exact-date rate, direct/inverted)
    │
    ▼
forecast.py: simulate balance for 90 days from request_date using recurring + confirmed + evidence-adjusted flows
    │
    ▼
payment_plans.py: generate candidates — full_payment / partial_payment / each installment option / wait /
                   not_recommended — each with 0-3 optional spending changes on eligible flexible events
    │
    ▼
verifier.py: filter to safe + rule-compliant + method-eligible candidates;
             rank via the 6-point order; resolve the 4-way status hierarchy
    │
    ▼
decision_engine.py: compute amount_safe_to_pay, earliest_date_for_full_payment (unassisted basis),
                     chosen affordability_status / recommended_payment_method / payment_plan /
                     spending_changes_needed
    │
    ▼
explanation.py: render decision_explanation from the verified numbers
    │
    ▼
verifier.py (final pass): re-validate the full output row against every output-contract rule
    │
    ▼
output.csv (repository root, 250 rows + header)
```

---

## 10. Risks / Ambiguities Discovered

1. **Status-vs-ranking interaction.** The spec's 6-point ranking list literally says "require no spending changes" ranks above "start payment earlier," yet sample `request_06` recommends a plan *with* a spending change (full payment today) over waiting until the natural, unassisted safe date with *no* changes. The most consistent reading: the affordability-status hierarchy (`affordable_now` > `affordable_with_plan` > `affordable_later` > `not_affordable`) is resolved **first** — pick the best status reachable at all — and the 6-point list only breaks ties **within** the plans that achieve that same best status/completion outcome. This must be encoded explicitly; a literal, flat application of the 6 criteria across all candidates (including "wait") would reproduce the wrong answer on this very sample.
2. **`financial_events.csv` has no `request_id` column.** "Relevant" events for a request are implicitly "this user's whole ledger," scoped by date (request_date ± 90 days, plus history for recurrence detection) — there is no explicit filter given in the data, so the forecast window boundaries must be inferred carefully from the rules (recurring pattern detection may need a longer look-back than 90 days; the forward window is exactly 90 days from `request_date` per spec).
3. **`linked_event_id` semantics are evidential, not authoritative** — per spec, "the link alone does not determine whether a row counts toward cash flow." Confirmed by data (a `linked_event_id` can point to a `cancelled` predecessor). Logic must always defer to each row's own `status`/`direction`, using the link only to avoid double-counting the same economic event told twice.
4. **Message/image conflicts weren't observed as true contradictions in this dataset** (the 3 message+image overlaps found all reinforce the image), so the conflict-resolution ordering rules could not be validated against a real disagreement here — code must still implement the general rule since the hidden evaluation set may exercise it more directly.
5. **`max_installment_months` cadence assumption.** The recon script approximated "months" as `number_of_payments` when `payment_frequency_days >= 27`, to flag likely-inadmissible installment options — this is a reasonable proxy given observed `payment_frequency_days` ∈ {28, 30, 31}, but the exact intended definition of "months" for a >31-day-cadence option (none observed) isn't specified and should be revisited if such options appear in the full 250-request set.
6. **No `pandas`/other libraries installed yet** in `.venv` — implementation should either vendor a `requirements.txt` and install, or stay stdlib-only for portability, per the "runnable from the terminal" and clear-setup-instructions requirements.
7. **Multilingual message text** (Indonesian observed for some IDR-currency users) means any text-based evidence extraction (LLM or otherwise) must handle non-English input correctly, not just parse English keywords.

---

## 11. Recommended Implementation Order (Phase 1+)

1. `data_loader.py` + `currency.py` — get clean, typed, indexed access to every file; this is pure plumbing and unblocks everything else.
2. `financial_state.py` (recurrence detection, conflict resolution, dedup via `linked_event_id`) using **only** `financial_events.csv` + `financial_profiles.csv` first (no messages/images yet) — validate against the 18 of 25 samples that don't depend on the 16 blank-amount events or heavy message amendments.
3. `forecast.py` + the 90-day minimum-balance check — validate `amount_safe_to_pay`/`earliest_date_for_full_payment` against samples with straightforward `affordable_now` / `affordable_later` outcomes (e.g. `request_01`, `request_03`).
4. `payment_plans.py` + `verifier.py` (bounds, schedule-match, ranking, status hierarchy) — validate against `installments`/`partial_payment` samples (`request_02`, `request_19`) and the spending-change sample (`request_06`) specifically, since it stress-tests the status-hierarchy ambiguity in §10.1.
5. `evidence.py` — add image-based amount extraction for the 16 blank-amount events, then message-based fact extraction, each behind the untrusted-input boundary; re-run against samples/requests that touch those specific `event_id`s.
6. `explanation.py` — template or LLM-drafted text from final verified numbers only.
7. Full-dataset run over all 250 `requests.csv` rows → `output.csv`; contract-validate (columns, bounds, one row per request); produce `evaluation/usage_report.md` from the run's actual token/call accounting.
8. Package `code.zip` (code + README + `evaluation/`), copy final `log.txt` as `chat_transcript`.

---

## Files Inspected

`README.md`, `problem_statement.md`, `AGENTS.md` (already loaded as project instructions), `code/main.py`, `code/evaluation/main.py`, `code/evaluation/usage_report.md`, and all nine files under `dataset/` (`requests.csv`, `sample_requests.csv`, `financial_profiles.csv`, `financial_events.csv`, `exchange_rates.csv`, `request_payment_options.csv`, `messages.csv`, `images.csv`, `output.csv`) plus `dataset/media/images/` (16 PNGs, existence/size/format-verified; one file's header bytes checked).

## Files Created

- `.gitignore` (repo root) — added to satisfy AGENTS.md §2 ("Keep `log.txt` in `.gitignore`"); it previously did not exist.
- `log.txt` (repo root, git-ignored) — session log per AGENTS.md §5.
- `evaluation/phase0_reconnaissance.md` — this report.
- Scratch analysis scripts used only to compute the statistics above were written to the session scratchpad directory outside the repository and are not part of this repo.

## Files Modified

None under `dataset/`. No dataset file, sample answer, or `output.csv` (root or `dataset/`) was changed.

## Dataset Modified?

**No.** Verified by re-reading this report's inputs directly from `dataset/` with no write operations issued against that directory.

## Unresolved Questions (for the user / Phase 1 kickoff)

- Confirm the status-hierarchy-before-ranking interpretation in §10.1 before implementing `verifier.py`'s selection logic, since it directly contradicts a literal reading of the 6-point list's ordering.
- Confirm whether `code/main.py` should target stdlib-only or if adding `pandas`/`Pillow`/an LLM SDK to a `requirements.txt` is acceptable (README doesn't forbid dependencies, just needs "clear setup and run instructions").
- Decide the LLM/vision provider(s) for `evidence.py` and `explanation.py` now, since `evaluation/usage_report.md` must report real usage from the **final full-dataset run** with whatever is chosen.

## Recommended Next Phase

**Phase 1: `data_loader.py` + `currency.py`.** Build the deterministic loading/indexing/currency layer described in §11 step 1, with no forecasting or decision logic yet, and smoke-test it by reprinting each sample request's fully-joined context (profile + relevant events + payment options + messages + images) for manual comparison against `sample_requests.csv`'s expected outputs.
