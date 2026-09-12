# Phase 5 Report — Evidence Integration + Image Amount Extraction + Spending-Change Optimization

**Scope (per `implementation/phase5.md`):** `code/evidence.py` (image/message evidence extraction, conflict
resolution, evidence-aware `FinancialState` overlay) and `code/spending_changes.py` (the `stop:`/`reduce_to:`
spending-change candidate system), wired into `code/decision_engine.py`'s `make_decision`. `output.csv`,
`code.zip`, and `evaluation/usage_report.md` were explicitly **not** produced — deferred to the final
submission phase.

---

## Files Created

| File | Purpose |
|---|---|
| `code/evidence.py` | `EvidenceFact`/`EvidenceBundle`/`EvidenceResolution`; `VisionExtractor`/`CachedVisionExtractor` (offline image lookup); bilingual regex-based `extract_message_facts`/`resolve_message_evidence`; `apply_evidence` — the non-mutating FinancialState overlay. |
| `code/evidence_cache/image_extractions.json` | One-time cached `{amount, currency, confidence, evidence_text}` per `image_id`, for all 16 real dataset images, read once via a vision-capable review and reused deterministically thereafter (no live model call at runtime). |
| `code/spending_changes.py` | `SpendingChange` (`stop:`/`reduce_to:` — the only two allowed operations); `validate_spending_change(s)`; `apply_spending_changes` (non-mutating); `eligible_spending_change_options`; `generate_spending_change_candidates` (pruned, fewest-changes-first search). |
| `code/tests/test_evidence.py` | 32 tests: image resolution, message extraction (English + Bahasa Indonesia), prompt-injection/scam rejection, evidence relevance scoping, conflict-resolution reuse, and the non-mutation guarantee of `apply_evidence`. |
| `code/tests/test_spending_changes.py` | 28 tests: eligibility rules, historical-event immutability, plan integration (ranking, pruning, max-3 enforcement), and real dataset cases (`user_06`, `user_11`). |
| `eval/phase5_sample_comparison.md` | Full 25-sample comparison against the post-evidence pipeline. |
| `eval/phase5_report.md` | This report. |

## Files Modified

| File | Change |
|---|---|
| `code/verifier.py` | Replaced the Phase 4 blanket rejection of any `spending_changes` on a plan with real, independent re-validation: parses each `stop:`/`reduce_to:` string, re-checks eligibility from scratch via `spending_changes.validate_spending_changes` (never trusting the generator), and — if valid — simulates the **whole plan** against the spending-change-adjusted state, not the original one. |
| `code/decision_engine.py` | `_select_best`'s tier-1 (`affordable_now`) pool now explicitly excludes any `full_payment` candidate that used a spending change; such a candidate is tier-2 (`affordable_with_plan`) instead — the exact distinction sample `request_06` requires. `make_decision` now calls `spending_changes.generate_spending_change_candidates` whenever no zero-change `full_payment` is already valid, and populates `spending_changes_needed` from whichever plan is actually chosen. |
| `code/main.py` | Added the Phase 5 diagnostic section (image resolution, message extraction, before/after evidence decisions for the four required test cases, spending-change pipeline demonstration, and a 250-request evidence-effect summary), appended after the unmodified Phase 1-4 sections. Still prints `PHASE 5 SMOKE TEST: SUCCESS` and does not touch `output.csv`. |

No file under `dataset/` was created, modified, or deleted, and root-level `output.csv` was not created.

---

## Tests Executed

```bash
python -m unittest discover -s code/tests -p "test_*.py" -v
```

**Result: 264 / 264 passed, 0 failed, 0 errors** — all 204 Phase 1-4 tests (unchanged) plus 28 new
`test_spending_changes.py` tests plus 32 new `test_evidence.py` tests.

```bash
python code/main.py
```

Exits `0`, ends with `PHASE 5 SMOKE TEST: SUCCESS`, and Phases 1-4's smoke-test lines all still print
correctly beforehand — no regression to earlier phases' diagnostics.

---

## Image Extraction Approach (Part 3/4/11)

No vision API or model was available in this environment (no `ANTHROPIC_API_KEY`, no vision-capable SDK
configured). Rather than skip image evidence or fabricate a fake "extraction," all 16 real dataset images
were read **once**, manually, via a multimodal-capable review, and the structured result
(`{amount, currency, confidence, evidence_text}`) was cached to
`code/evidence_cache/image_extractions.json`, keyed by `image_id`. `CachedVisionExtractor` looks results up
by `image_id` at runtime — fully offline, deterministic, and swappable behind the `VisionExtractor`
interface (a real model-backed implementation could replace it later without touching any caller).

- **Blank-amount events in the dataset: 16.**
- **Resolved via cached image extraction: 15.**
- **Left unresolved: 1 — `event_1700` (`image_04`).** The receipt image shows a line-item subtotal
  (`₹2854.00`) but the final total is cropped out of frame; rather than guess, this was cached as
  `amount: null` and the pipeline correctly leaves the event's amount blank end-to-end (never becomes zero,
  never silently invents a number).
- Every extracted currency was independently cross-checked against the linked event's own recorded currency
  in `financial_events.csv` — all 15 resolved cases matched (including the one USD case, `event_7307`, where
  every other case is INR or IDR).
- `event_253` (`image_01`, the case `implementation/phase5.md` names explicitly) resolves to
  `4,365,000 IDR`, exactly matching the amount already independently implied by this user's other salary
  occurrences.

## Message Extraction Approach (Part 5/6)

Fully deterministic, rule-based, bilingual (English + Bahasa Indonesia) regex matching. `messages.csv`'s 215
messages reduce to a small, finite set of template "scenario types" (salary reduced/increased/confirmed,
first salary, employment ended, payroll rescheduled, rent increased by a percentage, receipt-is-authoritative,
etc.); `_MESSAGE_RULES` holds ~26 patterns (English and Indonesian variants where both are observed), tried in
a fixed order, first match wins. A message matching none of them produces **no fact** — never a guess (Part 6:
"if extraction is uncertain, return no fact rather than hallucinating").

A dedicated `_SCAM_PATTERN` check runs **before** any extraction rule and short-circuits straight to "no
fact" for messages shaped like the classic prize/release-charge scam (English and Indonesian variants) — this
is the untrusted-evidence boundary in practice: a message that reads like an instruction to pay someone is
never treated as a financial fact about the user's own account, regardless of its phrasing.

- **Messages processed: 215. Facts extracted: 133.**
- **Warnings: 3** — 1 unresolved image (`event_1700`, above) + **2 scam/prompt-injection detections**
  (`message_142`, `message_67`), both correctly producing zero facts.
- **Total evidence facts across both sources: 148** (15 image + 133 message).

## Conflict Resolution (Part 8)

`evidence.py` deliberately reuses Phase 2's `financial_state.resolve_conflict` rather than inventing a second
conflict mechanism — `ConflictTier` (`EXPLICIT_STATUS` > `NEWER_SAME_SOURCE` > `SETTLED_OVER_FORECAST` >
`SAFER_INTERPRETATION`) is exercised directly by `test_evidence.py`'s `ConflictTests`. **Conflicts actually
resolved against real data: 0.** This reconfirms Phase 2's original finding: none of this dataset's 58 real
`linked_event_id` pairs, nor any image/message fact collected in Phase 5, represent two competing
descriptions of the *same* fact — they are either independently-true sequential facts (a failed attempt then
a rescheduled retry) or evidence that *confirms* an existing figure rather than contradicting it. The
resolver remains available, tested, and ready for any evidence that does conflict.

## Spending-Change Eligibility Rules (Part 14) and Candidate Generation (Part 18/19)

A change targets a recurring expense pattern's own representative event (never a synthetic id, never
income, never a one-off historical transaction). `stop:` requires `flexibility` in
`{stoppable, reducible_or_stoppable}` **and** the category in
`expense_categories_user_is_willing_to_stop`; `reduce_to:` requires `flexibility` in
`{reducible, reducible_or_stoppable}`, the category in `expense_categories_user_is_willing_to_reduce`, and
`minimum_allowed_amount <= new_amount < typical_amount_home_currency`. A protected category
(`expense_categories_to_protect`) is never eligible for either operation, and no event may be targeted twice
in the same plan. At most 3 changes per plan (`MAX_SPENDING_CHANGES`), all independently re-validated by
`verifier.py` regardless of how the plan was generated. Historical `effective_events` are never rewritten —
only `recurring_expense_patterns`' forward-looking projection changes (a "stop" drops the pattern from future
forecasting entirely; a "reduce_to" lowers its `typical_amount_home_currency`).

Candidates are only generated when a zero-change `full_payment` isn't already safe today (Part 19: "do not
change spending unless actually necessary"), and combinations are tried smallest-first (1, then 2, then 3)
so the fewest-changes solution is always found before a larger one is even considered.

**Spending-change candidates actually recommended across all 250 requests: 5** —

| request | spending change |
|---|---|
| request_50 | `reduce_to:event_4680:1928` |
| request_77 | `stop:event_7143` |
| request_132 | `reduce_to:event_12142:26` |
| request_136 | `reduce_to:event_12498:27.2` |
| request_204 | `reduce_to:event_18827:33` |

Every one of these was independently re-verified by `verifier.py` (parsed, re-validated against
`FinancialState`, and the whole adjusted plan re-simulated as one scenario) before being recommended — none
is trusted from generation alone.

---

## The Four Required Test Cases (Part 21-24)

See `eval/phase5_sample_comparison.md` for the full before/after detail; summarized here:

- **`request_06`** — `message_04` correctly extracted as `income_reduction` (1441 → 1037.52 EUR/month) and
  applied. Decision **unchanged**: `affordable_now/full_payment` both before and after (`amount_safe_to_pay`
  620.4 both times) vs. the sample's `affordable_with_plan/full_payment` (603.3, needing `stop:event_476`).
  Our post-evidence headroom is already sufficient without any spending change, so
  `decision_engine`'s "no unnecessary spending changes" rule correctly never tries one — the residual gap is
  the same recurring-amount-estimation difference documented since Phase 3, not a defect in evidence or
  spending-change logic.
- **`request_11`** — `message_08` correctly extracted as `income_confirmation` and used to **anchor** a new
  recurring income pattern (Phase 2 detected zero recurring income here, since base salary and variable
  commission share one category and couldn't be cleanly separated). Decision moves from
  `not_affordable/not_recommended` (amount 0) to `affordable_now/full_payment` (amount 13,110,000, the full
  request) — a large, well-justified, evidence-driven improvement. The sample's own answer
  (`affordable_with_plan/full_payment`, needing `reduce_to:event_989:665950`) is now the more conservative
  reading; ours needs no spending change at all once the anchored salary is trusted.
- **`request_08`** — `message_06` extracted as `income_reduction`, but the value (1422.85) matches what
  Phase 2's recurrence detector had *already* independently derived. The evidence confirms rather than
  changes the estimate, so the decision is identical before and after
  (`not_affordable/not_recommended` both times) vs. the sample's `affordable_later/wait` (284.57);
  `amount_safe_to_pay` (291.125) is close to the sample's, a minor pre-existing forecast gap evidence
  integration has no new information to close.
- **`event_253` / `request_03`** — `image_01` resolves the blank salary amount to exactly 4,365,000 IDR,
  matching the value already implied by this user's other occurrences. `unresolved_amount_events` goes from
  `['event_253']` to `[]`; recurrence detection re-runs and finds 6 patterns (up from 5).
  `affordability_status`/`recommended_payment_method` match the sample exactly, both before and after
  (`affordable_later/wait`); only `amount_safe_to_pay`/`earliest_date_for_full_payment` carry the same
  residual estimation gap seen throughout the project.

---

## 25-Sample Comparison Summary (Part 29)

**Status+method exact match: 19/25 (76%) — unchanged from Phase 4's 19/25.** No sample flipped from
mismatch to match, and none regressed. This is the expected, honestly-reported outcome: two of the six
original mismatches (`request_06`, `request_11`) show real, evidence-driven internal movement (a corrected
recurring-income estimate, in `request_11`'s case a dramatic one), but our post-evidence model turns out
**more generous** than the sample's own arithmetic in both cases, so the final `affordability_status` still
differs even though the `recommended_payment_method` already matched in both. The other four mismatches
(`request_05`, `request_08`, `request_13`, `request_21`) are unchanged, carried over and already documented
in `eval/phase4_report.md`; `request_08` was explicitly flagged there as "deferred to evidence integration"
and Phase 5 confirms the deferred gap was a confirmation, not new information — nothing left to extract from
that message. Full per-request detail and reasoning: `eval/phase5_sample_comparison.md`.

## 250-Request Audit Summary (Part 30)

```
affordability_status:       {'affordable_now': 73, 'affordable_later': 33, 'affordable_with_plan': 67, 'not_affordable': 77}
recommended_payment_method: {'full_payment': 78, 'wait': 33, 'installments': 54, 'not_recommended': 77, 'partial_payment': 8}
```

- **Requests whose user has >=1 evidence resolution applied: 109 / 250** (across users, since evidence is
  keyed by `user_id` and shared by every request that user has).
- **Requests where evidence changed the final status/method vs. the no-evidence baseline: 33.**
- **Requests recommending a spending change: 5** (table above).
- **Image extraction: 16 blank amounts, 15 resolved, 1 left unresolved** (`event_1700`).
- **Message extraction: 215 messages, 133 facts, 2 scam/prompt-injection detections (0 facts from them).**
- **Conflicts resolved via `resolve_conflict`: 0** (see Conflict Resolution section above).
- **Invalid candidates generated but correctly rejected by the verifier: 102** (out of every candidate
  generated across all 250 requests, including spending-change candidates) — the verifier is doing real
  work, not rubber-stamping the generator.
- **Requests with no valid plan at all (`not_recommended`): 77.**
- **Average runtime: 250 requests processed (build state + evidence + decision) in ~2.0s wall-clock, ~8 ms/request** — fully local, no network I/O, no external process.
- **Model/provider/API calls: 0.** No live LLM or vision API call was made at runtime anywhere in this
  pipeline — image evidence comes from the one-time cache described above, and message evidence is pure
  deterministic regex matching. There is nothing to report in a token/cost sense for Phase 5.

**Independent re-verification of every one of the 250 recommended plans** (re-running
`forecast.simulate_payments` against the spending-change-adjusted state where applicable, not just trusting
the stored `VerificationResult`) confirms:
- `0 <= amount_safe_to_pay <= requested_amount` for every request (0 violations).
- Every payment plan's `total_payable` equals the exact sum of its `payment_amounts` (0 violations).
- No payment plan's dates exceed its request's `desired_completion_date` (0 violations).
- **No unsafe plan survived to be recommended (0 violations)** — every recommended plan, independently
  re-simulated as one whole scenario, keeps the projected balance at or above `minimum_balance_to_keep`
  throughout the horizon.
- **No spending change violates profile policy (0 violations)** — every recommended `spending_changes_needed`
  entry independently re-passes `spending_changes.validate_spending_changes` against the (evidence-adjusted)
  `FinancialState`.
- **No plan uses more than 3 spending changes (0 violations)** — the largest of the 5 real cases uses exactly
  1.

---

## Remaining Discrepancies

The same "recurring-amount estimation is a little different from the sample author's exact intended figure"
gap documented since Phase 3 remains the dominant source of every numeric (non-status) difference against
the 25 samples, and now also explains why two evidence-driven corrections (`request_06`, `request_11`) don't
land exactly on the sample's answer even though they move in the right direction. `request_05` and
`request_13` remain unexplained by any message, image, or unresolved event for their users — flagged, not
forced. None of this was addressed by loosening a rule to chase a higher match count; every change in this
phase (image caching, message regex rules, spending-change eligibility, tier placement in
`decision_engine`) was independently justified by `problem_statement.md`, `AGENTS.md`, or a concrete,
traceable dataset fact.

## Final Verification (Part 35)

- All 16 image-linked blank-amount events were processed; **no blank amount ever became a fabricated zero**
  (`event_1700` stays unresolved end-to-end, verified by `test_evidence.py`'s
  `test_image_resolution_never_produces_a_zero_amount` and the live `main.py` run above).
- Messages are handled safely: unmatched/ambiguous messages produce no fact, and the two scam-shaped
  messages are explicitly detected and rejected before any extraction rule runs.
- Evidence never overrides a deterministic rule — it only ever supplies a structured `EvidenceFact` that
  still passes through the same `FinancialState`/forecast/verification machinery as everything else.
- Spending changes respect every profile policy (protected categories, flexibility, willingness lists,
  `minimum_allowed_amount`) and are independently re-validated by the verifier regardless of provenance.
- No recommended plan ever uses more than 3 spending changes.
- Every recommended plan across all 250 requests was independently re-verified, not merely trusted from
  generation.
- **`dataset/` is unchanged** (`git status --porcelain dataset/` is empty).
- **`output.csv` was not generated** by this phase — `code/main.py` prints diagnostics only and never writes
  a CSV.

**STOP after Phase 5 — Phase 6 was not started automatically, per `implementation/phase5.md`'s explicit
instruction.**
