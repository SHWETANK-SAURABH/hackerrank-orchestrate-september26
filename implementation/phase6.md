PHASE 6 — FINAL PRODUCTION RUN + OUTPUT VALIDATION + USAGE REPORT + SUBMISSION PACKAGE

We are completing the HackerRank Orchestrate "Buy or Wait?" challenge.

PHASES 1–5 ARE COMPLETE.

Read these before making changes:

- README.md
- problem_statement.md
- AGENTS.md
- evaluation/phase0_reconnaissance.md
- eval/phase1_report.md
- eval/phase2_report.md
- eval/phase3_report.md
- eval/phase4_report.md
- eval/phase5_report.md
- eval/phase5_sample_comparison.md
- code/main.py
- code/data_loader.py
- code/currency.py
- code/financial_state.py
- code/forecast.py
- code/payment_plans.py
- code/spending_changes.py
- code/evidence.py
- code/verifier.py
- code/decision_engine.py
- all existing tests under code/tests/

Do NOT unnecessarily rewrite working Phase 1–5 logic.

==================================================
GOAL
==================================================

This is the FINAL production phase.

The system must now:

1. run the complete pipeline against all 250 evaluation requests
2. generate the required root-level output.csv
3. validate every output row independently
4. run the complete test suite
5. produce evaluation/usage_report.md
6. preserve dataset/ unchanged
7. create the final code.zip submission package
8. ensure log.txt is maintained according to AGENTS.md
9. perform final submission-readiness checks

Do NOT start another development phase afterward.

==================================================
PART 1 — FINAL PIPELINE
==================================================

The final production flow must be:

    DataStore
       ↓
    FinancialState
       ↓
    EvidenceBundle
       ↓
    Evidence-aware State
       ↓
    Forecast
       ↓
    Candidate Payment Plans
       ↓
    Spending-change Candidates
       ↓
    Verifier
       ↓
    Decision Engine
       ↓
    Final Output Row

Every one of the 250 evaluation requests must pass through the same pipeline.

Do NOT create special-case logic for individual request IDs.

Do NOT hardcode sample answers.

==================================================
PART 2 — OUTPUT FILE
==================================================

Generate:

    output.csv

at the repository ROOT.

Do NOT use:

    dataset/output.csv

The required columns and exact order are:

    request_id
    amount_safe_to_pay
    affordability_status
    recommended_payment_method
    payment_plan
    earliest_date_for_full_payment
    spending_changes_needed
    decision_explanation

There must be exactly:

    250 data rows

plus:

    1 header row

==================================================
PART 3 — OUTPUT SCHEMA
==================================================

For every row:

request_id:
    Must correspond to a real evaluation request.

amount_safe_to_pay:
    Must be numeric.
    Must satisfy:
        0 <= amount_safe_to_pay <= requested_amount

affordability_status:
    Must be exactly one of:

        affordable_now
        affordable_with_plan
        affordable_later
        not_affordable

recommended_payment_method:
    Must be exactly one of:

        full_payment
        partial_payment
        installments
        wait
        not_recommended

payment_plan:
    Must accurately describe the selected plan.

earliest_date_for_full_payment:
    Must follow the challenge's baseline definition:
    calculated without optional spending changes and independently of payment
    method preferences.

spending_changes_needed:
    Must contain only valid:
        stop:event_id
        reduce_to:event_id:new_amount

decision_explanation:
    Must explain the actual deterministic evidence/reasoning behind the result.

==================================================
PART 4 — PAYMENT PLAN SERIALIZATION
==================================================

Use one deterministic serialization format consistently.

For full payment:

    full_payment:<date>:<amount>

For partial payment:

    exactly two payment legs:
    first payment on request date
    second payment on earliest full-payment date

For installments:

    serialize the exact supplied payment option structure.

For wait:

    represent the future full-payment date and amount.

For not_recommended:

    use an explicit safe representation consistent with README/problem
    requirements and the existing implementation.

Do NOT invent a new format that conflicts with the starter repository.

Before writing output.csv, inspect the existing README/AGENTS/problem statement
for any required payment_plan formatting and preserve that exact requirement.

==================================================
PART 5 — SPENDING CHANGE SERIALIZATION
==================================================

spending_changes_needed may contain at most 3 entries.

Allowed syntax:

    stop:event_id

or:

    reduce_to:event_id:new_amount

Do not output synthetic recurrence IDs.

Do not output unsupported operations.

If no spending changes are needed, use the representation required by the
starter specification.

Do NOT modify the dataset to make event IDs available.

==================================================
PART 6 — EARLIEST FULL PAYMENT DATE
==================================================

This field is NOT simply the date of the selected payment plan.

It must represent:

    the earliest date on which the FULL requested amount can be safely paid

under the baseline scenario:

- no optional spending changes
- independent of payment-method preferences

Use the Phase 3/4 implementation.

Verify that the final output does not accidentally use a spending-change
enabled date for this field.

==================================================
PART 7 — DECISION EXPLANATIONS
==================================================

Every explanation must be generated from actual decision data.

It should mention relevant factors such as:

- requested amount
- safe amount
- affordability status
- selected payment method
- relevant timing
- spending changes if any
- important evidence when it materially affected the result

Do NOT allow an LLM to invent explanations that contradict the actual plan.

If explanations are generated using templates, prefer deterministic templates.

The explanation must never claim:

- an amount was extracted when it was unresolved
- a spending change occurred when none was selected
- a payment was made
- an external account was checked
- live market/bank information was consulted

==================================================
PART 8 — FINAL VERIFICATION ENGINE
==================================================

Create or extend a dedicated final validation function, for example:

    validate_output_row(...)

It must independently validate every generated row.

At minimum check:

1. request_id exists
2. amount_safe_to_pay is numeric
3. amount_safe_to_pay >= 0
4. amount_safe_to_pay <= requested_amount
5. status is allowed
6. payment method is allowed
7. status/method combination is valid
8. payment plan is internally consistent
9. payment amounts sum correctly
10. payment dates are valid
11. no payment occurs after desired_completion_date
12. partial payment has exactly two payments
13. partial-payment conditions are satisfied
14. installments exactly match an allowed request_payment_options row
15. installment months respect max_installment_months
16. wait is only used when full payment becomes safely possible later
17. wait respects full_payment preference requirement
18. spending changes are valid
19. no more than 3 spending changes
20. spending changes obey profile policies
21. earliest full-payment date is baseline-derived
22. final recommended plan is safe under the full forecast
23. final plan is independently re-simulated
24. output fields are not contradictory

Do not trust only the stored VerificationResult.

Re-run the actual relevant calculations.

==================================================
PART 9 — STATUS/METHOD CONSISTENCY
==================================================

Preserve the Phase 4/5 hierarchy:

    affordable_now
    affordable_with_plan
    affordable_later
    not_affordable

Do not downgrade or upgrade a result merely to match samples.

Use the actual final deterministic decision.

Examples:

- affordable_now should represent a valid immediate solution under the
  challenge's definition.
- affordable_with_plan should represent a valid plan requiring a plan and/or
  permitted spending changes.
- affordable_later should represent waiting until full payment becomes safe.
- not_affordable should have no valid solution under the allowed rules.

==================================================
PART 10 — 250 REQUEST RUN
==================================================

Run all 250 evaluation requests.

Record diagnostics before writing output.csv:

    status counts
    method counts
    spending-change count
    evidence-applied count
    unresolved evidence count
    verifier rejection count
    no-valid-plan count
    runtime

Compare against Phase 5 diagnostics.

If counts change, investigate whether the change is caused by an intentional
final-pipeline correction.

Do NOT silently accept unexplained changes.

==================================================
PART 11 — OUTPUT CSV VALIDATION
==================================================

After generating output.csv:

Read it back from disk.

Verify:

    exactly 250 rows
    exact header
    no duplicate request_id
    no missing request_id
    all request IDs correspond to the 250 evaluation requests

Then validate every row against the original request and profile data.

Produce a validation summary.

The final run must report:

    schema violations = 0
    numeric violations = 0
    payment-plan violations = 0
    deadline violations = 0
    unsafe-plan violations = 0
    spending-change violations = 0
    duplicate/missing request IDs = 0

If any violation occurs:

    STOP
    fix the implementation
    regenerate output.csv
    validate again

Do not ship a file with known violations.

==================================================
PART 12 — DECIMAL / MONEY SAFETY
==================================================

Use Decimal for financial arithmetic.

Do not introduce binary floating-point arithmetic into final decision logic.

Before CSV serialization:

- normalize Decimal values deterministically
- avoid scientific notation if the expected format disallows it
- do not round values unless required by the specification
- preserve exact payment-plan arithmetic

==================================================
PART 13 — DATASET IMMUTABILITY
==================================================

The dataset is read-only.

Before final run:

    git status --porcelain dataset/

After final run:

    git status --porcelain dataset/

The result must be empty.

Do not:

- rewrite CSV files under dataset/
- overwrite images
- modify dataset/output.csv
- add generated caches under dataset/

Generated caches must remain under code/ or another non-dataset location.

==================================================
PART 14 — USAGE REPORT
==================================================

Now create:

    evaluation/usage_report.md

It must describe the FINAL FULL-DATASET RUN.

Do not simply copy Phase 5 statistics if the final run differs.

Include:

1. execution date/time
2. pipeline version/phase
3. requests processed
4. successful requests
5. failed requests
6. providers/models used
7. number of model calls
8. input tokens
9. output tokens
10. total tokens
11. average tokens/request
12. estimated cost
13. average cost/request
14. image-evidence calls
15. message-evidence calls
16. whether external APIs were used
17. runtime
18. caching behavior
19. fallback behavior
20. errors/warnings

For this implementation, if the final pipeline still uses the Phase 5
offline image cache and deterministic regex message extraction, explicitly
report:

    live model/API calls = 0

Do not fabricate token counts or costs.

If no provider/model was used:

    provider/model = none
    model calls = 0
    tokens = 0
    estimated cost = 0

Do not include secrets or API keys.

==================================================
PART 15 — TEST SUITE
==================================================

Run:

    python -m unittest discover -s code/tests -p "test_*.py" -v

All tests must pass.

Expected baseline from Phase 5:

    264 / 264 passed

If new final-validation tests are added, update the expected total accordingly.

Do not delete tests to make the suite pass.

==================================================
PART 16 — FINAL SMOKE TEST
==================================================

Run:

    python code/main.py

Ensure all previous smoke tests still pass.

The final main.py should now also demonstrate the production output generation
or invoke the same production pipeline used for output.csv.

Avoid maintaining two separate decision implementations.

There must be ONE authoritative production path.

==================================================
PART 17 — SAMPLE REGRESSION CHECK
==================================================

Re-run all 25 solved samples using the final pipeline.

Do NOT optimize rules for sample matching.

Generate or update:

    eval/final_sample_comparison.md

Include:

- request_id
- expected status
- generated status
- expected method
- generated method
- expected amount
- generated amount
- expected earliest date
- generated earliest date
- match/mismatch
- explanation

Report the final exact status+method match count.

Compare it with Phase 5's:

    19/25 (76%)

If it changes, explain why.

Do not hardcode sample answers.

==================================================
PART 18 — FINAL SAFETY AUDIT
==================================================

Perform an independent audit of all 250 output rows.

Check:

    0 <= amount_safe_to_pay <= requested_amount

Check:

- every recommended plan is safe
- every payment plan sums correctly
- every installment plan is an actual allowed option
- every partial payment has exactly two payments
- every plan meets the deadline
- every wait plan has a valid future safe date
- no spending-change policy violation
- no more than 3 spending changes
- no protected expense is changed
- no inflexible expense is changed
- no historical expense is rewritten
- no synthetic event ID is emitted
- unresolved evidence never becomes zero
- cancelled/failed transactions are not incorrectly treated as cash flow
- pending credits are not incorrectly treated as available money
- duplicate events are not double-counted
- foreign-currency events use the correct dated exchange rate

Report all violations.

Expected:

    0

==================================================
PART 19 — PERFORMANCE
==================================================

Measure:

- total runtime
- state construction time
- evidence processing time
- forecast time
- decision time
- CSV generation time
- validation time

Report total and average/request.

Do not sacrifice correctness for micro-optimization.

==================================================
PART 20 — LOG.TXT
==================================================

Follow AGENTS.md exactly for chat transcript/log requirements.

Ensure:

    log.txt

contains the required final conversation/transcript information.

Do not put secrets in log.txt.

If AGENTS.md says log.txt is gitignored, keep that behavior.

==================================================
PART 21 — CODE QUALITY
==================================================

Before packaging:

- remove temporary debug prints
- remove dead experimental code
- remove unused imports
- ensure imports work from repository root
- ensure imports work under the expected execution command
- keep deterministic behavior
- preserve clear module boundaries

Do not remove useful diagnostics from reports.

==================================================
PART 22 — FINAL PACKAGE
==================================================

Create:

    code.zip

The package must contain the required source code and supporting files needed
by the submission.

Inspect AGENTS.md and README.md for the exact packaging expectation.

At minimum ensure the package contains the final:

    code/

implementation

including:

    main.py
    data_loader.py
    currency.py
    financial_state.py
    forecast.py
    payment_plans.py
    spending_changes.py
    evidence.py
    verifier.py
    decision_engine.py

and any required supporting package/cache/config files.

Do not include:

- .venv
- __pycache__
- .pyc files
- secrets
- API keys
- unnecessary temporary files
- giant generated artifacts
- dataset modifications

If the starter instructions specify an exact code.zip structure, follow that
instead.

==================================================
PART 23 — PACKAGE VALIDATION
==================================================

After creating code.zip:

Inspect its contents.

Confirm:

- required source files exist
- no secrets exist
- no virtual environment exists
- no __pycache__ exists
- evidence cache required by the offline implementation is included
- package can be extracted cleanly

Do not assume code.zip is correct merely because zip creation succeeded.

==================================================
PART 24 — REPRODUCIBILITY TEST
==================================================

Run the production pipeline twice from the same dataset.

Compare the two generated outputs.

They should be identical.

If they differ:

    investigate nondeterminism

Potential causes:

- unordered iteration
- random model output
- timestamps inserted into output.csv
- unstable sorting
- floating-point arithmetic
- filesystem ordering

output.csv itself must be deterministic.

==================================================
PART 25 — FINAL FILE CHECK
==================================================

At the end, the repository should contain:

    output.csv
    code.zip
    evaluation/usage_report.md
    eval/final_sample_comparison.md

and the existing source/test/report files.

Confirm these exist.

==================================================
PART 26 — FINAL GIT/DATASET CHECK
==================================================

Run:

    git status --short

Inspect every changed file.

Specifically verify:

    dataset/

has no modifications.

If dataset/ changed:

    revert only the unintended dataset changes

without modifying legitimate project work elsewhere.

==================================================
PART 27 — FINAL REPORT
==================================================

Create:

    eval/phase6_report.md

Include:

1. Final pipeline summary.
2. Test suite result.
3. 250-request result.
4. Final status counts.
5. Final payment-method counts.
6. Spending-change count.
7. Evidence statistics.
8. Final output.csv row count.
9. Output schema validation result.
10. Independent safety-audit result.
11. 25-sample final comparison.
12. Runtime.
13. Usage/cost statistics.
14. Reproducibility result.
15. code.zip contents.
16. Dataset immutability result.
17. Any remaining known discrepancies.
18. Confirmation that no sample answers were hardcoded.
19. Confirmation that Phase 6 is complete.

==================================================
PART 28 — FINAL ACCEPTANCE CRITERIA
==================================================

Do NOT declare Phase 6 complete unless ALL are true:

[ ] All tests pass.
[ ] output.csv exists at repository root.
[ ] output.csv has exactly 250 data rows.
[ ] Header matches required schema exactly.
[ ] Every request_id is unique and valid.
[ ] Every amount_safe_to_pay is within [0, requested_amount].
[ ] Every payment plan is internally valid.
[ ] Every recommended plan is independently verified safe.
[ ] Every plan respects desired_completion_date.
[ ] Installment plans match supplied payment options.
[ ] Partial payments contain exactly two payments.
[ ] Spending changes are valid and <= 3.
[ ] No protected/inflexible expense is modified.
[ ] No unresolved blank amount became zero.
[ ] Evidence remains bounded and cannot override deterministic rules.
[ ] dataset/ is unchanged.
[ ] evaluation/usage_report.md describes the actual final run.
[ ] code.zip exists and contains the required code.
[ ] code.zip contains no secrets/.venv/__pycache__.
[ ] Final pipeline is reproducible.
[ ] final_sample_comparison.md exists.
[ ] phase6_report.md exists.
[ ] log.txt requirements from AGENTS.md are satisfied.

==================================================
IMPORTANT
==================================================

This is the FINAL PHASE.

Do not start another phase.

Do not chase sample-match percentage by changing financial rules without
evidence from the specification.

Correctness, safety, reproducibility, and submission compliance take priority
over matching the solved examples.

At the very end print:

    ========================================
    PHASE 6 COMPLETE
    ========================================
    output.csv: READY
    code.zip: READY
    usage_report.md: READY
    final validation: PASSED
    dataset unchanged: YES
    ========================================

STOP.