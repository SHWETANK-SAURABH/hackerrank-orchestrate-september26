PHASE 3 — 90-DAY FINANCIAL FORECAST + SAFETY SIMULATION

We are continuing the HackerRank Orchestrate "Buy or Wait?" challenge in
the existing cloned repository.

PHASE 1 and PHASE 2 ARE COMPLETE.

Before starting, read:

- README.md
- problem_statement.md
- AGENTS.md
- evaluation/phase0_reconnaissance.md
- eval/phase1_report.md
- eval/phase2_report.md
- code/data_loader.py
- code/currency.py
- code/financial_state.py

Do not rewrite working Phase 1 or Phase 2 functionality unnecessarily.

==================================================
GOAL
==================================================

Implement ONLY the deterministic 90-day financial forecasting/safety layer.

Create:

    code/forecast.py

This module must simulate a user's financial position forward through the
required forecast horizon and determine whether a proposed amount can be
safely paid on specific dates.

The forecast must enforce:

    balance >= minimum_balance_to_keep

at every relevant point in the forecast.

The forecast will later be used by Phase 4 for affordability decisions.

==================================================
DO NOT IMPLEMENT YET
==================================================

Do NOT implement:

- affordability_status
- amount_safe_to_pay as a final decision
- recommended_payment_method
- payment_plan generation
- installment selection
- partial-payment selection
- spending-change optimization
- final plan ranking
- decision explanations
- output.csv
- LLM calls
- vision calls
- image amount extraction

This phase is ONLY the forecasting/safety engine.

==================================================
PART 1 — FORECAST MODEL
==================================================

Create appropriate dataclasses, for example:

    ForecastEntry
    ForecastResult
    ForecastScenario

Names may differ if a better design is appropriate.

ForecastResult should expose enough information for later phases to answer:

- starting balance
- minimum balance
- forecast start date
- forecast end date
- balance at each relevant date
- cash-flow entries
- minimum projected balance
- date of minimum projected balance
- whether the balance ever violates the minimum
- reason for a violation if one occurs

Use Decimal for all money.

Never use float for financial calculations.

==================================================
PART 2 — FORECAST HORIZON
==================================================

The problem requires a 90-day safety forecast.

Build the forecast from the relevant request context:

    request_date
    desired_completion_date

The engine must support forecasting at least through:

    request_date + 90 days

and must also be capable of evaluating a desired completion date that falls
within that horizon.

Do NOT assume the request date is today's actual system date.

Use the request's dates from the dataset.

The forecast must be deterministic and reproducible.

==================================================
PART 3 — STARTING BALANCE
==================================================

The starting balance must come from:

    financial_profile.current_available_balance

Do NOT reconstruct it from historical transactions.

The safety floor must come from:

    financial_profile.minimum_balance_to_keep

The forecast must never allow the balance to fall below this floor.

For example:

    starting balance = 50,000
    minimum balance = 10,000

means the usable safety headroom is:

    40,000

but future cash flows may reduce or increase that headroom.

==================================================
PART 4 — HISTORICAL EVENTS
==================================================

Do NOT replay the entire historical ledger from the beginning.

Phase 2 already provides:

    FinancialState

Use it as the source of truth.

Historical effective events are useful for deriving recurring patterns,
but they must not be double-counted as future cash flows.

The forecast should start from the profile's current available balance and
project forward from the request date/current financial-state boundary.

Do not subtract historical settled expenses again.

==================================================
PART 5 — FUTURE KNOWN CASH FLOWS
==================================================

Include future known cash movements from FinancialState.

This includes:

- future/scheduled income
- future/scheduled expenses

For each event:

1. Use its actual known amount.
2. Convert to home currency if necessary.
3. Use the appropriate settlement/event date already normalized by Phase 2.
4. Apply its direction correctly.
5. Preserve the event ID as provenance.

Do not treat failed/cancelled transactions as future cash flows.

Do not treat pending credits as available current cash.

Do not silently turn unresolved amounts into zero.

==================================================
PART 6 — RECURRING INCOME
==================================================

Project recurring income patterns detected by Phase 2.

For each recurrence:

- determine future occurrence dates
- determine the expected amount
- use the user's home currency amount
- generate occurrences only inside the forecast horizon
- preserve source event IDs/pattern metadata

Use the recurrence pattern's detected frequency/interval.

Do NOT invent a recurrence if Phase 2 did not detect one.

Do NOT use current date.

Do NOT generate an occurrence outside the forecast horizon.

--------------------------------------------------
IMPORTANT
--------------------------------------------------

If a known future/scheduled income event corresponds to a recurring income
occurrence, avoid double-counting it.

A known future event should take precedence over generating another synthetic
occurrence for the same cash flow.

Use deterministic matching based on:

- user
- direction
- category/event type
- expected date proximity
- description/pattern identity

Keep the matching conservative.

If there is ambiguity, prefer avoiding double counting.

==================================================
PART 7 — RECURRING EXPENSES
==================================================

Project recurring expense patterns detected by Phase 2.

For each recurrence:

- determine future occurrence dates
- determine expected amount
- preserve category
- preserve flexibility
- preserve minimum_allowed_amount
- preserve source event IDs

These recurring expenses are part of the baseline financial forecast.

DO NOT reduce or stop them in Phase 3.

Spending changes belong to a later phase.

--------------------------------------------------
KNOWN FUTURE EXPENSE DUPLICATION
--------------------------------------------------

If a known future/scheduled expense corresponds to a recurring expense
occurrence, do not double-count it.

Use conservative deterministic matching.

Known future event wins over a synthetic recurrence occurrence.

==================================================
PART 8 — PENDING TRANSACTIONS
==================================================

Pending transactions require careful treatment.

Pending EXPENSES:

- represent a future cash obligation
- should reduce projected balance when they settle/occur
- must not be subtracted twice if they later correspond to a known future
  settled/scheduled event

Pending INCOME/CREDITS:

- MUST NOT be treated as currently available cash
- should not be counted as confirmed income unless the data explicitly
  establishes the future settlement
- do not rely on pending credits to make a purchase safe today

The forecast must expose pending items separately.

==================================================
PART 9 — SCHEDULED INCOME SAFETY
==================================================

Confirmed future/scheduled income may be used in future affordability
calculations.

However:

    future income != current available cash

Therefore:

A purchase that is unsafe today but safe after a confirmed future salary
should become safe only on/after the date that salary is actually received.

This behavior is critical for the later "affordable_later" status.

==================================================
PART 10 — CASH-FLOW ORDERING
==================================================

When multiple cash flows occur on the same date, use a deterministic order.

Do not let Python dictionary ordering or CSV ordering determine financial
results.

Define and document an explicit order.

The safest approach is to process:

1. known/scheduled cash movements by their actual settlement semantics
2. recurring cash movements
3. pending settlements
4. test/request-specific hypothetical payments

However, carefully inspect the problem specification and actual event fields
before choosing the exact ordering.

The key invariant is:

    no intermediate balance may violate minimum_balance_to_keep.

If two flows occur on the same date, evaluate the conservative ordering
where necessary so that a temporary negative/safety-floor breach cannot be
hidden.

Document the chosen rule.

==================================================
PART 11 — HYPOTHETICAL PAYMENT TEST
==================================================

Implement a reusable function such as:

    can_safely_pay(
        financial_state,
        request_date,
        amount,
        desired_completion_date
    )

This function should answer:

"Can this amount be paid while maintaining the minimum balance throughout
the required forecast?"

It should NOT choose a payment method.

It should simply simulate the hypothetical payment.

For a full payment on request_date:

    balance_on_request_date -= amount

Then continue the forecast.

Return a structured result containing:

- safe/unsafe
- minimum resulting balance
- date of minimum balance
- first violation date, if any
- projected balances
- relevant cash-flow entries

==================================================
PART 12 — PAYMENT ON FUTURE DATE
==================================================

The engine must also support:

    can_safely_pay_on_date(amount, payment_date)

This will later be used for:

- affordable_later
- installment plans
- partial payments

Do not implement those decision policies yet.

The forecasting engine only needs to answer whether a hypothetical cash
outflow on a particular date is safe.

==================================================
PART 13 — DESIRED COMPLETION DATE
==================================================

A proposed purchase must be completed by:

    desired_completion_date

The forecast must therefore distinguish:

1. Amount is safe now.
2. Amount becomes safe later.
3. Amount never becomes safe within the required horizon.

Do NOT convert these into affordability statuses yet.

Return raw forecast facts.

For example:

    safe_now = False
    first_safe_date = 2026-10-14
    safe_by_deadline = True

This is exactly the information Phase 4 will consume.

==================================================
PART 14 — MAXIMUM SAFE AMOUNT
==================================================

Implement a deterministic capability to determine the maximum amount that
could be paid safely at a specified payment date.

For example:

    maximum_safe_payment(
        financial_state,
        payment_date,
        deadline
    )

The returned value must satisfy:

    0 <= safe_amount

and:

    balance never < minimum_balance_to_keep

throughout the forecast after applying the hypothetical payment.

Do NOT use an arbitrary brute-force dollar-by-dollar search.

Use the structure of the forecast to derive the maximum safe amount.

At minimum, the result should account for:

- starting balance
- all cash flows before/after payment date
- minimum balance
- future obligations
- recurring income
- recurring expenses

The maximum safe payment should be deterministic.

==================================================
PART 15 — FUTURE SAFE DATE
==================================================

Implement:

    earliest_safe_payment_date(
        financial_state,
        amount,
        start_date,
        deadline
    )

It should return the earliest date on which paying the specified amount is
safe through the required forecast horizon/deadline.

If no date is safe, return None.

The result must be based only on confirmed/forecastable financial information
available in FinancialState.

Do not use spending changes.

Do not use payment method preferences.

Do not use installment options.

Do not use messages/images.

==================================================
PART 16 — EARLIEST FULL-PAYMENT DATE RULE
==================================================

This is especially important.

The eventual output field:

    earliest_date_for_full_payment

must be calculated independently of:

- user's payment method preference
- payment options
- spending changes

Therefore Phase 3 must expose the raw baseline:

    earliest_safe_payment_date

based only on the financial state and baseline forecast.

Later phases may use spending changes to make an actual recommended plan
possible earlier.

Do NOT let those later optimizations leak into this Phase 3 function.

==================================================
PART 17 — 90-DAY SAFETY INVARIANT
==================================================

For every forecast scenario:

    projected_balance(date) >= minimum_balance_to_keep

must hold for every relevant point in time.

If it fails:

    mark scenario unsafe

Do not hide a temporary violation merely because the balance recovers later.

Example:

    minimum = 10,000

    Day 10: 12,000
    Day 11:  8,000
    Day 12: 15,000

This is unsafe because Day 11 breached the floor.

==================================================
PART 18 — UNRESOLVED AMOUNTS
==================================================

There are 16 blank-amount financial events.

Phase 2 deliberately leaves these unresolved.

Phase 3 must NOT:

- treat them as zero
- guess their amount
- fabricate a forecast amount

If an unresolved event is relevant to the future forecast:

- mark the forecast as having unresolved financial evidence
- expose the event ID
- do not silently assume a favorable value

If it is historical and not relevant to future cash flow, it should not
corrupt the current starting balance.

Design the result so later evidence resolution can replace the unresolved
amount without rewriting the forecast engine.

==================================================
PART 19 — SAFETY WITH UNCERTAINTY
==================================================

Do not assume uncertain future cash inflows are available unless Phase 2
classified them as confirmed/forecastable.

In particular:

- pending income cannot rescue a purchase
- failed income cannot rescue a purchase
- cancelled income cannot rescue a purchase
- unrealized investment valuation cannot rescue a purchase
- future confirmed salary can support a future payment date

Use the financially safer interpretation where uncertainty remains.

==================================================
PART 20 — TESTS
==================================================

Create:

    code/tests/test_forecast.py

Include tests for at least:

1. Starting balance comes from profile.

2. Minimum balance is enforced.

3. Historical settled expenses are not replayed and double-counted.

4. Future scheduled income increases projected balance on its date.

5. Future scheduled expense decreases projected balance on its date.

6. Recurring income is projected.

7. Recurring expenses are projected.

8. Known scheduled event is not double-counted with recurrence.

9. Pending expense is treated as a future obligation according to its
   settlement semantics.

10. Pending credit does not count as current cash.

11. Failed event is not projected.

12. Cancelled event is not projected.

13. Unresolved amount is never treated as zero.

14. A payment safe today remains safe through the full forecast horizon.

15. A payment that causes a future safety-floor violation is rejected.

16. Payment becomes safe after a confirmed future income event.

17. earliest_safe_payment_date returns the first valid date.

18. earliest_safe_payment_date returns None when no valid date exists.

19. maximum_safe_payment never exceeds the amount that keeps the balance
    above the safety floor.

20. maximum_safe_payment is deterministic.

21. Same-date cash-flow ordering is deterministic.

22. A temporary safety-floor violation is detected even if the balance
    recovers later.

23. Desired completion date is respected.

24. A payment after the desired completion date is not considered valid.

25. Future income after the 90-day horizon is not used.

26. Forecast horizon is deterministic from request dates.

27. Foreign-currency future cash flow is converted using Phase 1/2 currency
    normalization.

28. Forecast provenance retains source event IDs.

29. No spending changes are applied.

30. No payment-method preferences affect the raw safety result.

Also include synthetic scenarios specifically designed to catch:

- double-counted salary
- double-counted recurring expense
- pending credit incorrectly treated as cash
- future obligation missed because balance looks safe today
- temporary minimum-balance breach
- salary arriving exactly on a payment date
- expense occurring exactly on a payment date

==================================================
PART 21 — REAL DATA VALIDATION
==================================================

Extend code/main.py with a Phase 3 smoke test.

Keep Phase 1 and Phase 2 smoke tests working.

The Phase 3 smoke test should:

1. Load DataStore.
2. Load CurrencyConverter.
3. Build FinancialState for representative users.
4. Build a 90-day forecast.
5. Print:
   - request/user
   - starting balance
   - minimum balance
   - forecast horizon
   - number of projected entries
   - minimum projected balance
   - date of minimum balance
   - unresolved relevant events
6. Test at least one amount that is safe.
7. Test at least one amount that is unsafe because of a future obligation.
8. Demonstrate a future-safe date if the real dataset contains one.
9. Demonstrate maximum safe amount.
10. Confirm no spending changes or payment methods are involved.

Print:

    PHASE 3 SMOKE TEST: SUCCESS

Do NOT generate output.csv.

Do NOT produce affordability statuses.

==================================================
PART 22 — PERFORMANCE
==================================================

There are 25,342 historical events and 275 users.

Do not repeatedly scan the entire dataset.

Use Phase 2's:

- user-level FinancialState
- recurring patterns
- future event lists
- pending lists

The forecast for a user should operate only on that user's relevant state.

Cache reusable baseline forecasts if useful.

==================================================
PART 23 — DETERMINISM
==================================================

The exact same inputs must produce the exact same forecast.

Do not use:

- randomness
- current wall-clock date
- live financial data
- live FX
- LLM calls
- nondeterministic ordering

Sort events explicitly using stable keys such as:

- date
- settlement date
- event ID

==================================================
PART 24 — API DESIGN
==================================================

Expose a clean public API for Phase 4.

At minimum something conceptually similar to:

    build_forecast(financial_state, start_date, horizon_days=90)

    simulate_payment(
        financial_state,
        payment_date,
        amount,
        end_date
    )

    can_safely_pay(
        financial_state,
        payment_date,
        amount,
        end_date
    )

    maximum_safe_payment(
        financial_state,
        payment_date,
        end_date
    )

    earliest_safe_payment_date(
        financial_state,
        amount,
        start_date,
        deadline
    )

Names can differ.

The API must make Phase 4 able to ask safety questions without knowing how
the forecast is internally constructed.

==================================================
PART 25 — DOCUMENTATION
==================================================

Document in code comments/docstrings:

- forecast horizon
- starting balance semantics
- minimum balance semantics
- event inclusion rules
- pending transaction rules
- recurring-event projection rules
- duplicate prevention
- same-day ordering
- unresolved amount handling
- maximum-safe-payment calculation
- earliest-safe-date calculation

Keep the documentation concise but explicit.

==================================================
PART 26 — DO NOT MODIFY DATASET
==================================================

Absolutely do not modify:

    dataset/

Do not modify:

- CSV files
- images
- dataset/output.csv

Do not create fake predictions.

==================================================
PART 27 — FINAL VERIFICATION
==================================================

Run:

    python -m unittest discover -s code/tests -p "test_*.py" -v

Then run:

    python code/main.py

Confirm:

- all Phase 1 tests still pass
- all Phase 2 tests still pass
- all Phase 3 tests pass
- smoke test succeeds
- dataset remains unchanged
- root output.csv does not exist or is not modified
- no affordability logic was added

==================================================
DELIVERABLE REPORT
==================================================

Create:

    eval/phase3_report.md

Include:

1. Files created/modified.
2. Tests executed and results.
3. Forecast model implemented.
4. Event inclusion/exclusion rules.
5. Recurrence projection behavior.
6. Pending transaction handling.
7. Same-day ordering rule.
8. Maximum-safe-payment method.
9. Earliest-safe-payment-date method.
10. Number of real users successfully forecast.
11. Number of projected future cash-flow entries.
12. Number of unresolved events encountered.
13. Any data-quality warnings.
14. Performance observations.
15. Confirmation dataset/ was not modified.
16. Confirmation no affordability/payment-plan/spending-change logic was
    implemented.

STOP AFTER PHASE 3.

DO NOT START PHASE 4 AUTOMATICALLY.