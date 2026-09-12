PHASE 4 — PAYMENT PLANS + VERIFIER + BUY/WAIT DECISION LOGIC

We are continuing the HackerRank Orchestrate "Buy or Wait?" challenge in
the existing cloned repository.

PHASE 1, PHASE 2, AND PHASE 3 ARE COMPLETE.

Before starting, read:

- README.md
- problem_statement.md
- AGENTS.md
- evaluation/phase0_reconnaissance.md
- eval/phase1_report.md
- eval/phase2_report.md
- eval/phase3_report.md
- code/data_loader.py
- code/currency.py
- code/financial_state.py
- code/forecast.py

Do not rewrite working previous phases unnecessarily.

==================================================
GOAL
==================================================

Implement:

    code/payment_plans.py
    code/verifier.py
    code/decision_engine.py

This phase converts the Phase 3 financial safety facts into actual candidate
payment plans and a deterministic final affordability decision.

The decision engine must eventually produce:

- amount_safe_to_pay
- affordability_status
- recommended_payment_method
- payment_plan
- earliest_date_for_full_payment

However, do NOT yet integrate messages/images or generate the final
output.csv. Evidence integration and final pipeline work belong to later
phases.

==================================================
PART 1 — REQUIRED OUTPUT SEMANTICS
==================================================

The final decision engine must use exactly these affordability statuses:

    affordable_now
    affordable_with_plan
    affordable_later
    not_affordable

And exactly these payment methods:

    full_payment
    partial_payment
    installments
    wait
    not_recommended

Do not invent additional output status/method values.

==================================================
PART 2 — REQUEST INPUT
==================================================

For each request, use:

- request_id
- user_id
- request_date
- request_type
- requested_amount
- desired_completion_date
- allows_partial_payment
- request_text

Retrieve:

- user's FinancialState
- user's payment method preferences
- user's max installment months
- request's supplied payment options

Do not infer payment preferences from unrelated data.

==================================================
PART 3 — PAYMENT PLAN DATA MODEL
==================================================

Create a clear dataclass such as:

    PaymentPlan

It should contain enough information to verify a candidate plan.

Suggested fields:

- method
- amount_paid
- number_of_payments
- payment_dates
- payment_amounts
- total_payable
- financing_fee
- full_payment_date
- source_payment_option_id
- spending_changes
- valid
- invalid_reasons

Also create a decision/result structure such as:

    DecisionResult

containing:

- request_id
- amount_safe_to_pay
- affordability_status
- recommended_payment_method
- payment_plan
- earliest_date_for_full_payment
- spending_changes_needed
- decision_explanation placeholder/raw facts if appropriate

Do NOT generate polished natural-language explanations in this phase.

==================================================
PART 4 — FULL PAYMENT
==================================================

Generate a full-payment candidate.

A full-payment candidate means:

    requested_amount

is paid on:

    request_date

Verify it using Phase 3:

    can_safely_pay(...)

The payment is valid only if the entire forecast remains:

    balance >= minimum_balance_to_keep

through the required horizon/deadline.

If safe:

    amount_safe_to_pay = requested_amount
    method = full_payment

Do not use payment-method preferences to calculate
earliest_date_for_full_payment.

==================================================
PART 5 — MAXIMUM SAFE AMOUNT
==================================================

Use Phase 3's:

    maximum_safe_payment(...)

to determine how much can safely be paid at a specified date.

Never exceed:

    requested_amount

Therefore:

    amount_safe_to_pay <= requested_amount

and:

    amount_safe_to_pay >= 0

Do not use dollar-by-dollar brute force.

==================================================
PART 6 — PARTIAL PAYMENT
==================================================

A partial-payment plan is valid ONLY when ALL of these conditions hold:

1. request allows partial payment.

2. The user accepts partial payment / the request's supported payment
   behavior permits it according to the actual dataset semantics.

3. Safe amount is:

       > 0
       < requested_amount

4. The remaining amount can be fully paid by:

       desired_completion_date

5. Exactly TWO payments are made.

Payment 1:

    safe amount on request_date

Payment 2:

    requested_amount - safe amount

    on earliest date where the remainder can safely be paid

The two payment amounts MUST sum exactly to requested_amount.

Use Decimal arithmetic.

Never round in a way that makes the two payments fail to sum exactly.

The second payment date must not exceed desired_completion_date.

If the remainder cannot safely be paid by the deadline:

    partial payment is invalid.

==================================================
PART 7 — INSTALLMENT PLANS
==================================================

Use ONLY installment/payment options supplied in:

    request_payment_options.csv

Do NOT invent an installment schedule.

For each option:

- payment_option_id
- payment_method
- payment_amount
- number_of_payments
- first_payment_date
- payment_frequency_days
- financing_fee
- total_payable_amount

must be preserved exactly.

An installment candidate is valid only if:

1. User accepts installments.

2. The request has that supplied option.

3. Option's number of months/payments is within:

       max_installment_months

4. The actual schedule is reproduced exactly.

5. Every payment remains safe under Phase 3 forecasting.

6. The full requested amount is completed by the desired completion date.

7. Total payments exactly match the supplied option's total payable amount.

Do not modify:

- number of payments
- payment dates
- payment amounts
- financing fee
- total payable amount

Do not "improve" a supplied installment option.

==================================================
PART 8 — INSTALLMENT SCHEDULE
==================================================

Construct the exact payment dates from:

    first_payment_date
    payment_frequency_days
    number_of_payments

BUT compare carefully with the actual supplied row.

If the dataset's representation requires dates/amounts to be interpreted
differently, follow the actual CSV schema and problem statement rather than
inventing semantics.

The verifier must validate the resulting schedule against the original
PaymentOption.

==================================================
PART 9 — WAIT PLAN
==================================================

A wait plan means:

- do not pay today
- pay the full requested amount on a future safe date

Wait is valid only if:

1. full payment becomes safe at some future date
2. full_payment is an accepted payment method for the user
3. the payment date is on or before desired_completion_date

Use:

    earliest_safe_payment_date(...)

for the raw future safe date.

Do NOT use spending changes to calculate this date.

Do NOT use installment options to calculate this date.

==================================================
PART 10 — NOT RECOMMENDED
==================================================

If no valid way exists to complete the purchase safely by the desired
completion date:

    method = not_recommended

and:

    affordability_status = not_affordable

Do not fabricate a plan.

==================================================
PART 11 — AFFORDABILITY STATUS HIERARCHY
==================================================

This is a critical interpretation point.

Do NOT blindly rank all candidates using one flat comparison.

Use the following semantic hierarchy:

--------------------------------------------------
1. AFFORDABLE_NOW
--------------------------------------------------

If the requested amount can be safely paid in full on request_date:

    status = affordable_now
    method = full_payment

This is the strongest outcome.

--------------------------------------------------
2. AFFORDABLE_WITH_PLAN
--------------------------------------------------

If full payment today is unsafe but a valid plan can complete the purchase
by the desired completion date, use:

    affordable_with_plan

This may include:

- partial_payment
- installments
- a plan enabled by allowed spending changes in the later evidence/
  optimization phase

For THIS phase, only use plans actually supported by the currently available
financial/profile/payment-option information.

--------------------------------------------------
3. AFFORDABLE_LATER
--------------------------------------------------

If full payment is not safe today but becomes safe later by the deadline:

    affordable_later
    method = wait

--------------------------------------------------
4. NOT_AFFORDABLE
--------------------------------------------------

If no valid plan can complete the purchase safely by the deadline:

    not_affordable
    method = not_recommended

Do not call something "affordable_later" if waiting would miss the
desired_completion_date.

==================================================
PART 12 — IMPORTANT SAMPLE INTERPRETATION
==================================================

The Phase 0/3 analysis found that request_06 is:

    affordable_with_plan / full_payment

because spending changes can make it safe to pay now, while its baseline
earliest full-payment date is later.

Therefore do NOT assume:

    "no spending changes" always beats "spending changes"

in one flat global ranking.

The semantic affordability status must be established first.

Within a status, use the specified ranking rules.

==================================================
PART 13 — PLAN RANKING
==================================================

When multiple valid candidates produce the same affordability status, rank
them using the problem statement's preference order:

1. Complete by deadline.
2. No spending changes.
3. Minimize total amount paid.
4. Start earlier.
5. Fewer payments.
6. Lowest payment_option_id.

Implement this as a deterministic comparator.

Be careful:

- "no spending changes" is a ranking criterion within comparable plans.
- It must not override the semantic distinction between
  affordable_now / affordable_with_plan / affordable_later.

Document this explicitly.

==================================================
PART 14 — USER PAYMENT METHOD PREFERENCES
==================================================

Immediate methods are eligible only if the user accepts them.

Use:

    payment_methods_user_will_consider

from financial_profiles.csv.

Examples:

If user does not accept:

    full_payment

do not recommend full_payment merely because the forecast says it is safe.

If user accepts:

    installments

then supplied installment options may be considered.

If the user does not accept installments:

    do not generate/recommend installments.

Wait is allowed only if full_payment is accepted, because waiting still
means the purchase is eventually paid in full.

IMPORTANT:

Payment-method preferences affect the recommended plan.

They MUST NOT affect:

    earliest_date_for_full_payment

That field is a raw financial-safety fact.

==================================================
PART 15 — MAX INSTALLMENT MONTHS
==================================================

If:

    max_installment_months is None

the user does not consider installments.

If it is populated:

    reject any installment option exceeding the user's maximum.

Do not silently truncate an installment schedule.

==================================================
PART 16 — EARLIEST FULL-PAYMENT DATE
==================================================

Every DecisionResult must expose:

    earliest_date_for_full_payment

calculated from Phase 3 baseline financial safety only.

It must be independent of:

- payment method preferences
- supplied installment options
- spending changes
- LLM evidence
- recommendation ranking

Use:

    earliest_safe_payment_date(...)

from Phase 3.

If the full amount can never be safely paid within the required horizon:

    earliest_date_for_full_payment = None

Do NOT replace this field with the recommended plan's actual completion date.

==================================================
PART 17 — PAYMENT-PLAN VERIFIER
==================================================

Create:

    code/verifier.py

This is extremely important.

Do not trust generated plans.

The verifier must independently validate a candidate plan.

For every candidate, verify:

1. payment dates are valid
2. payment dates are ordered
3. no payment occurs before request_date
4. no payment occurs after desired_completion_date
5. payment amounts are positive where required
6. sum of payment amounts is correct
7. total payable is correct
8. payment method is supported
9. installment option matches supplied data exactly
10. user accepts the method
11. every payment is safe under the forecast
12. minimum balance is never violated
13. no unsupported spending change is present
14. partial payment has exactly two payments
15. partial payment first payment is the safe amount
16. partial payment second payment equals the remaining amount
17. full payment has exactly one payment
18. full payment amount equals requested amount
19. wait has exactly one future full payment
20. wait date is safe
21. completion date requirement is satisfied

Return structured verification results:

    valid: bool
    errors: [...]
    warnings: [...]

Never silently repair an invalid candidate.

==================================================
PART 18 — EXACT INSTALLMENT VERIFICATION
==================================================

For an installment candidate originating from payment_option_id:

compare against the original supplied option.

Verify exact:

- payment_option_id
- payment method
- number of payments
- payment amount(s)
- first payment date
- frequency
- financing fee
- total payable

If any value differs:

    reject the candidate.

Do not round an option's values and then call them equal.

Use Decimal comparisons for money.

==================================================
PART 19 — FORECAST VERIFICATION OF MULTI-PAYMENT PLANS
==================================================

For multi-payment plans, do NOT independently check each payment in
isolation.

Simulate the entire plan through the forecast.

Example:

Payment 1 may be safe alone.

Payment 2 may be safe alone.

But both together may cause a future safety-floor violation.

The verifier must reject such a plan.

Use the Phase 3 simulation machinery.

==================================================
PART 20 — PARTIAL PAYMENT CALCULATION
==================================================

For a partial-payment candidate:

1. Determine maximum safe amount on request_date.
2. Clamp to requested_amount.
3. Require:

       0 < safe_amount < requested_amount

4. Remaining:

       remaining = requested_amount - safe_amount

5. Find earliest date where remaining can be paid safely while considering
   the already-scheduled first payment.

This is important.

Do NOT calculate the second payment against a completely fresh forecast that
forgets the first payment.

The two-payment plan must be simulated together.

6. Require second payment date <= desired_completion_date.

7. Verify total exactly equals requested_amount.

==================================================
PART 21 — WAIT CALCULATION
==================================================

For wait:

1. Find earliest baseline safe date for requested_amount.
2. Require date <= desired_completion_date.
3. Require full_payment accepted.
4. Simulate the actual one-payment wait plan.
5. Verify it independently.

==================================================
PART 22 — PLAN CANDIDATE GENERATION
==================================================

Create something conceptually like:

    generate_candidate_plans(request, state, forecast)

Candidates:

A. full payment
B. partial payment if allowed
C. every valid supplied installment option
D. wait if eligible
E. not_recommended fallback

Do not generate plans the user cannot accept.

Do not generate plans not supported by the request.

Do not generate invented installment schedules.

==================================================
PART 23 — SPENDING CHANGES
==================================================

DO NOT implement spending-change optimization in this phase.

The final plan data model may contain:

    spending_changes = []

but this phase must leave it empty.

Phase 5 will integrate evidence and spending-change optimization.

Do not prematurely stop/reduce recurring expenses.

==================================================
PART 24 — REQUEST AMOUNT
==================================================

The requested amount is already in the user's home currency according to the
problem specification.

Do not convert the request amount through FX.

Financial event amounts are converted.

The purchase request amount is not.

==================================================
PART 25 — ZERO / EDGE CASES
==================================================

Handle safely:

- requested_amount == 0
- requested_amount < 0 → invalid request/error
- requested_amount exactly equals maximum safe amount
- maximum safe amount == 0
- desired_completion_date == request_date
- payment date exactly equals a future salary date
- installment option with one payment
- installment option whose total payable includes a financing fee
- user with max_installment_months = None
- user accepting only wait/full payment
- user accepting no immediate payment methods

Do not produce invalid output values.

==================================================
PART 26 — TESTS
==================================================

Create:

    code/tests/test_payment_plans.py
    code/tests/test_verifier.py
    code/tests/test_decision_engine.py

At minimum test:

FULL PAYMENT
1. Safe full payment today → affordable_now/full_payment.
2. Unsafe full payment today → full payment rejected.
3. User rejecting full_payment → full payment not recommended.
4. Full payment exactly at safety boundary succeeds.
5. Full payment one unit above boundary fails.

PARTIAL PAYMENT
6. Partial payment disabled → no partial plan.
7. Partial payment enabled with safe amount > 0 → candidate generated.
8. Partial amount must be < requested amount.
9. Exactly two payments.
10. Two payment amounts sum exactly to requested amount.
11. Second payment cannot exceed desired completion date.
12. Second payment is checked with first payment already applied.
13. No valid second date → partial plan rejected.

INSTALLMENTS
14. Valid supplied installment option accepted.
15. Unsupported invented installment schedule rejected.
16. User max months rejects oversized option.
17. max_installment_months=None rejects installments.
18. Exact payment amounts verified.
19. Exact payment count verified.
20. Exact dates/frequency verified.
21. Exact total payable verified.
22. Financing fee mismatch rejected.
23. Multi-payment installment forecast verified as one complete scenario.

WAIT
24. Future safe date exists → wait candidate.
25. Future safe date after deadline → wait rejected.
26. User rejecting full_payment → wait rejected.
27. Wait uses baseline earliest safe date.
28. Wait plan passes complete forecast verification.

STATUS
29. Safe today → affordable_now.
30. Valid plan → affordable_with_plan.
31. Safe only later → affordable_later.
32. No valid plan → not_affordable.

RANKING
33. Earlier completion wins.
34. No spending changes wins within comparable candidates.
35. Lower total payable wins.
36. Earlier start wins.
37. Fewer payments wins.
38. Lower payment_option_id wins.
39. Ranking is deterministic.

EARLIEST DATE
40. Earliest full-payment date does not depend on payment preferences.
41. Earliest full-payment date does not depend on installment options.
42. Earliest full-payment date does not depend on spending changes.
43. Earliest date is not replaced by recommended plan completion date.

VERIFIER
44. Rejects date after deadline.
45. Rejects payment before request date.
46. Rejects incorrect payment sum.
47. Rejects unsafe individual payment.
48. Rejects plan that becomes unsafe cumulatively.
49. Rejects unsupported method.
50. Rejects invalid installment option.
51. Rejects invalid partial plan.
52. Rejects invalid wait plan.

EDGE CASES
53. Zero requested amount.
54. Negative requested amount.
55. Same-day salary does not rescue a same-day payment if Phase 3 says it is
    unsafe.
56. Future obligation can invalidate an otherwise safe-looking plan.

==================================================
PART 27 — REAL DATA CROSS-CHECK
==================================================

Before finalizing, run the decision engine against all 25 solved sample
requests.

This is NOT permission to hardcode sample answers.

Use the samples as diagnostic evidence only.

Produce a comparison table containing at least:

- request_id
- sample status
- generated status
- sample method
- generated method
- sample earliest full-payment date
- generated earliest full-payment date
- match/mismatch
- reason if mismatch can be explained

Investigate mismatches.

Do NOT modify rules merely to force sample matching.

In particular, investigate:

    request_05

which Phase 3 identified as a meaningful unresolved discrepancy.

Document whether Phase 4 resolves it or whether it remains unexplained.

==================================================
PART 28 — REAL DATA SAFETY AUDIT
==================================================

Run the decision engine for all 250 evaluation requests.

Do NOT write output.csv yet.

Collect diagnostics:

- number affordable_now
- number affordable_with_plan
- number affordable_later
- number not_affordable
- number full_payment
- number partial_payment
- number installments
- number wait
- number not_recommended
- invalid candidate count
- verifier rejection count
- requests with no valid plan
- requests with unresolved evidence

Look for impossible values:

- amount_safe_to_pay < 0
- amount_safe_to_pay > requested_amount
- payment sums not matching
- completion after deadline
- unsafe plans marked valid

Fix logic bugs before proceeding.

==================================================
PART 29 — MAIN.PY
==================================================

Extend code/main.py with a Phase 4 diagnostic section.

Keep Phase 1–3 smoke tests working.

The Phase 4 section should:

1. Build FinancialState.
2. Build Forecast.
3. Generate candidate plans.
4. Verify candidates.
5. Run DecisionEngine.
6. Print representative decisions.
7. Print sample cross-check summary.
8. Print 250-request diagnostic summary.

Do NOT generate final output.csv yet.

Print:

    PHASE 4 SMOKE TEST: SUCCESS

==================================================
PART 30 — DETERMINISM
==================================================

Decision results must be deterministic.

No:

- randomness
- LLM
- live data
- live FX
- current-date dependence
- arbitrary dictionary ordering

Sort candidates before ranking.

The same request and dataset must produce exactly the same result.

==================================================
PART 31 — DATASET SAFETY
==================================================

Do NOT modify:

    dataset/

Do not modify:

- CSV files
- images
- dataset/output.csv

Do not generate final output.csv.

==================================================
PART 32 — FINAL VERIFICATION
==================================================

Run:

    python -m unittest discover -s code/tests -p "test_*.py" -v

Then:

    python code/main.py

Confirm:

- all previous tests still pass
- all Phase 4 tests pass
- sample cross-check is completed
- all 250 evaluation requests can be processed without crashes
- every generated plan is independently verified
- no invalid plan is marked valid
- dataset remains unchanged
- output.csv is NOT created/modified
- Phase 5 evidence/LLM/image integration has NOT been started

==================================================
DELIVERABLE REPORT
==================================================

Create:

    eval/phase4_report.md

Include:

1. Files created/modified.
2. Tests executed and results.
3. Candidate plan types implemented.
4. Verifier rules.
5. Decision/status hierarchy.
6. Ranking rules.
7. Payment preference handling.
8. Installment validation behavior.
9. Partial-payment behavior.
10. Wait behavior.
11. Earliest-full-payment-date behavior.
12. 25-sample cross-check table/summary.
13. 250-request diagnostic summary.
14. request_05 investigation.
15. Any unresolved discrepancies.
16. Any invalid-plan/verifier issues discovered.
17. Confirmation dataset/ was not modified.
18. Confirmation output.csv was not generated.
19. Confirmation no evidence/LLM/vision integration was implemented.

STOP AFTER PHASE 4.

DO NOT START PHASE 5 AUTOMATICALLY.

