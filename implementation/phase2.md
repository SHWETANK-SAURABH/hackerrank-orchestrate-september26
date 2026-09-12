PHASE 2 — FINANCIAL STATE + RECURRENCE + CONFLICT RESOLUTION

We are continuing the HackerRank Orchestrate "Buy or Wait?" challenge in the existing cloned repository.

Phase 1 is COMPLETE.

Before starting:
- Read README.md
- Read problem_statement.md
- Read AGENTS.md
- Read evaluation/phase0_reconnaissance.md
- Read eval/phase1_report.md
- Inspect the existing code/data_loader.py and code/currency.py
- Do not rewrite working Phase 1 functionality unnecessarily.

==================================================
GOAL
==================================================

Implement ONLY the financial-state/reconciliation layer.

Create:

    code/financial_state.py

This phase must:

1. Convert raw financial events into a normalized financial ledger.
2. Resolve financial-event conflicts.
3. Resolve linked/amended/settled event relationships.
4. Detect recurring income and expenses.
5. Classify events according to their financial treatment.
6. Produce a deterministic per-user financial state that later forecasting
   can consume.

DO NOT implement:

- 90-day forecasting
- affordability decisions
- amount_safe_to_pay
- payment-plan generation
- spending-change optimization
- plan ranking
- final output.csv generation
- LLM calls
- vision calls
- image amount extraction
- natural-language explanation generation

Those belong to later phases.

==================================================
IMPORTANT DATA FACTS
==================================================

Use the Phase 0/Phase 1 findings rather than assumptions.

Important:

- financial_events.csv has NO request_id.
- Events are associated with users through user_id.
- linked_event_id points to an earlier same-user event.
- A linked event does NOT automatically mean the original event should be
  removed or reversed.
- The CURRENT ROW'S status and direction determine its own financial treatment.
- Blank event amounts must remain unresolved in this phase.
- Blank amounts will be resolved later using images.
- Financial events can use INR, ZAR, IDR, USD, and EUR.
- Convert financial event amounts to the user's home currency using the
  Phase 1 CurrencyConverter and the appropriate settlement date.
- Do not use live FX.
- Do not use approximate/nearest exchange rates.

==================================================
PART 1 — NORMALIZED LEDGER
==================================================

Create appropriate dataclasses.

For example:

    NormalizedEvent
    RecurrencePattern
    FinancialState

Names can differ if a better design is appropriate.

NormalizedEvent should preserve:

- event_id
- user_id
- event_type
- description
- category
- direction
- original_amount
- original_currency
- amount_home_currency
- event_date
- settlement_date
- status
- linked_event_id
- flexibility
- minimum_allowed_amount
- source/raw-event reference if useful
- financial treatment/classification

Do NOT destroy the original information.

==================================================
PART 2 — CURRENCY NORMALIZATION
==================================================

For every event whose amount is known:

    original amount + original currency
                ↓
        CurrencyConverter
                ↓
        user's home currency

Use:

    settlement_date

as the FX date when a settlement date exists, consistent with the problem
specification.

If the event is already in the user's home currency, preserve the amount.

If the amount is None:

    amount_home_currency = None

Do NOT treat None as zero.

Do NOT guess the amount.

Do NOT use image processing in this phase.

==================================================
PART 3 — EVENT FINANCIAL TREATMENT
==================================================

Create a deterministic classification for each event.

At minimum distinguish:

- usable income
- usable expense
- pending income
- pending expense
- scheduled/forecast income
- cancelled
- failed
- informational/non-cash
- unresolved amount

The exact internal enum/names are up to you.

The classification must be based on the actual event fields:

- event_type
- direction
- status
- amount
- dates
- description/category where needed

Do not make affordability decisions here.

--------------------------------------------------
Status rules
--------------------------------------------------

Follow the problem specification.

In particular:

FAILED:
    Do not count as an actual cash flow.

CANCELLED:
    Do not count as an actual cash flow.

PENDING:
    Treat according to the event's direction and the financial-state model,
    but do not incorrectly treat pending credits as currently available cash.

SCHEDULED/FUTURE INCOME:
    Keep available as a future forecastable event, but it must not be
    treated as current available balance.

SETTLED/COMPLETED/POSTED:
    Treat as actual financial activity where the event semantics support it.

If the dataset contains other statuses, inspect them and implement explicit
handling rather than silently assuming.

==================================================
PART 4 — LINKED EVENTS
==================================================

Implement linked-event analysis.

For every event with:

    linked_event_id != None

retrieve the referenced event through the DataStore index.

Validate:

- referenced event exists
- referenced event belongs to the same user
- referenced event precedes the linking event when the data semantics require it

Do NOT simply delete the linked event.

Instead determine the financial treatment from the actual pair.

Examples of relationships that may need reconciliation:

- amendment
- settlement
- reversal
- cancellation
- replacement
- correction

The current event's:

- status
- direction
- amount
- event_type
- dates

must be considered.

==================================================
PART 5 — CONFLICT RESOLUTION
==================================================

Implement deterministic conflict-resolution rules based on the problem
statement.

When multiple records represent competing information:

Priority should be:

1. explicit cancellation / settlement / amendment
2. newer event from the same source
3. settled/completed information over estimate/forecast
4. financially safer interpretation when ambiguity remains

IMPORTANT:

Do not invent a transaction history that the data does not support.

Keep the raw records intact.

The purpose of reconciliation is to determine which events are financially
effective for the normalized state.

Add tests specifically covering conflict-resolution behavior.

==================================================
PART 6 — RECURRING INCOME
==================================================

Detect recurring income from historical events.

Do NOT use an LLM.

Use deterministic signals such as:

- repeated event descriptions
- repeated categories
- repeated event_type
- repeated direction
- similar amounts
- regular date spacing
- settlement behavior
- user-level history

The detector should be conservative.

A one-off transaction must not become recurring merely because it has a
similar description.

For each detected recurring income pattern, store information such as:

- description/category
- typical amount
- currency/home-currency amount
- frequency
- typical interval
- next expected occurrence if determinable
- confidence/evidence
- source event IDs

Do not invent future income when the evidence is insufficient.

==================================================
PART 7 — RECURRING EXPENSES
==================================================

Detect recurring expenses using the same conservative deterministic approach.

Capture:

- category
- description
- typical amount
- home-currency amount
- frequency
- typical interval
- next expected occurrence if determinable
- flexibility
- minimum_allowed_amount
- evidence/source event IDs

This information will later be used by the spending-change and forecast phases.

Do NOT optimize or reduce expenses yet.

Do NOT decide whether an expense should be stopped/reduced yet.

Just identify recurring patterns and preserve their policy metadata.

==================================================
PART 8 — FLEXIBILITY + PROTECTED EXPENSES
==================================================

Preserve the following event/profile information for later phases:

From financial events:

- flexibility
- minimum_allowed_amount
- category
- direction

From financial profiles:

- financial_priorities
- expense_categories_to_protect
- expense_categories_user_is_willing_to_reduce
- expense_categories_user_is_willing_to_stop

Do NOT actually stop/reduce anything in this phase.

The financial-state output should make these fields available to later phases.

==================================================
PART 9 — USER FINANCIAL STATE
==================================================

Implement something like:

    build_financial_state(user_id)

It should return a FinancialState containing, as appropriate:

- user_id
- home_currency
- current_available_balance
- minimum_balance_to_keep
- effective historical events
- recurring income patterns
- recurring expense patterns
- future/scheduled income events
- pending transactions
- relevant profile policies
- reconciliation metadata
- warnings/data-quality flags

Do NOT calculate affordability.

Do NOT calculate the final safe-to-pay amount.

Do NOT forecast 90 days yet.

==================================================
PART 10 — CURRENT BALANCE
==================================================

Be careful with:

    current_available_balance

This value comes from financial_profiles.csv.

Do NOT reconstruct the current balance from historical events unless the
problem specification explicitly requires that for a later phase.

For this phase:

- preserve the profile's current_available_balance as the starting balance
- normalize it to Decimal
- preserve minimum_balance_to_keep

Historical events should be used to establish future/recurring behavior,
not to arbitrarily overwrite the profile's reported current balance.

==================================================
PART 11 — PENDING / FAILED / CANCELLED
==================================================

Explicitly expose these categories in FinancialState so the forecast layer
can make the correct decision later.

In particular:

- failed transactions must not reduce available cash
- cancelled transactions must not reduce available cash
- pending expenses must not be mistaken for already-settled historical
  expenses
- pending credits must NOT be treated as available cash
- future scheduled income must remain future income

Do not collapse all of these into one generic category.

==================================================
PART 12 — UNRESOLVED IMAGE AMOUNTS
==================================================

There are 16 financial events with blank amounts.

For those events:

- preserve the event
- mark amount as unresolved
- do not count the amount as income or expense
- do not treat it as zero
- do not attempt OCR/image analysis
- expose it in FinancialState diagnostics

The later evidence phase will resolve these.

==================================================
PART 13 — TESTS
==================================================

Create:

    code/tests/test_financial_state.py

Tests must cover at minimum:

1. Home-currency event remains unchanged.

2. Foreign-currency event is converted using settlement date.

3. Missing amount remains None.

4. Failed expense does not become an effective expense.

5. Cancelled transaction does not become an effective cash flow.

6. Pending credit is not considered current available cash.

7. Future scheduled income is represented as future income.

8. Linked event is resolved through event_id.

9. Invalid linked_event_id is detected.

10. Cross-user linked_event_id is detected.

11. Recurring income pattern is detected when the dataset provides clear
    repeated evidence.

12. Recurring expense pattern is detected when the dataset provides clear
    repeated evidence.

13. One-off transaction is not incorrectly classified as recurring.

14. Flexibility and minimum_allowed_amount are preserved.

15. Protected/reducible/stoppable profile categories are preserved.

16. Current profile balance remains the starting balance.

17. No unresolved blank amount is converted to zero.

18. Conflict-resolution precedence is deterministic.

Also add at least one synthetic test fixture for a tricky linked-event
scenario where relying only on linked_event_id would produce the wrong
financial treatment.

==================================================
PART 14 — REAL DATA VALIDATION
==================================================

Add a Phase-2 smoke test.

Update code/main.py only enough to run Phase 2 validation.

It should:

1. Load DataStore.
2. Load CurrencyConverter.
3. Build FinancialState for several representative users.
4. Print concise diagnostics:
   - home currency
   - starting balance
   - effective event count
   - unresolved amount count
   - recurring income count
   - recurring expense count
   - pending count
   - future income count
5. Demonstrate at least one foreign-currency normalization.
6. Demonstrate at least one recurring pattern from the real dataset.
7. Demonstrate linked-event resolution.
8. Confirm no blank amount was treated as zero.
9. Print:

    PHASE 2 SMOKE TEST: SUCCESS

Do NOT generate output.csv.

Do NOT make affordability decisions.

==================================================
PART 15 — PERFORMANCE
==================================================

There are 25,342 financial events.

Do not repeatedly scan all events for every event.

Use the indexes already provided by DataStore.

Build user-level state efficiently.

A reasonable implementation may:

- group events by user once
- normalize each event once
- build recurrence candidates once per user
- cache FinancialState per user

==================================================
PART 16 — DETERMINISM
==================================================

The same dataset must always produce the same FinancialState.

Avoid:

- random logic
- nondeterministic ordering
- LLM decisions
- live data
- current-date assumptions unless explicitly required by the dataset/spec

Sort records deterministically where ordering matters.

==================================================
PART 17 — DO NOT MODIFY DATASET
==================================================

Absolutely do not modify:

    dataset/

Do not modify:

- CSV files
- images
- dataset/output.csv

Do not generate fake predictions.

==================================================
PART 18 — FINAL VERIFICATION
==================================================

Run:

    python -m unittest discover -s code/tests -p "test_*.py" -v

Then run:

    python code/main.py

Confirm:

- all existing Phase 1 tests still pass
- all Phase 2 tests pass
- smoke test succeeds
- dataset remains unchanged
- output.csv is not generated/modified

==================================================
DELIVERABLE REPORT
==================================================

Create:

    eval/phase2_report.md

Include:

1. Files created/modified.
2. Tests executed and results.
3. Number of users whose FinancialState was successfully built.
4. Number of effective events.
5. Number of unresolved blank-amount events.
6. Number of recurring income patterns.
7. Number of recurring expense patterns.
8. Number of pending events.
9. Number of scheduled/future income events.
10. Linked-event validation results.
11. Conflict-resolution behavior implemented.
12. Any data-quality warnings.
13. Confirmation that dataset/ was not modified.
14. Confirmation that no affordability/forecast/payment-plan logic was added.

STOP AFTER PHASE 2.

Do NOT start Phase 3 automatically.
