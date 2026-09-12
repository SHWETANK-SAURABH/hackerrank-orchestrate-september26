PHASE 1 — DATA LOADER + CURRENCY LAYER

We are continuing the HackerRank Orchestrate "Buy or Wait?" challenge inside the existing cloned repository.

Phase 0 reconnaissance has already been completed.

Read:
- evaluation/phase0_reconnaissance.md
- README.md
- problem_statement.md
- AGENTS.md

The Phase 0 report contains the actual dataset schema and relationships. Use the actual dataset, not assumptions.

==================================================
GOAL
==================================================

Implement ONLY:

1. data_loader.py
2. currency.py

These modules must provide clean, deterministic, typed, indexed access to the dataset.

DO NOT implement:

- financial-state reconstruction
- recurrence detection
- forecasting
- amount_safe_to_pay
- affordability decisions
- payment-plan generation
- spending-change optimization
- plan ranking
- LLM calls
- vision calls
- decision explanations

Those belong to later phases.

==================================================
PART 1 — data_loader.py
==================================================

Create:

code/data_loader.py

Use Python's standard library where practical. Do not add dependencies unless they are genuinely required.

Load these files from the repository's dataset/ directory:

- requests.csv
- sample_requests.csv
- financial_profiles.csv
- financial_events.csv
- exchange_rates.csv
- request_payment_options.csv
- messages.csv
- images.csv

Do NOT modify any dataset file.

The loader should determine dataset paths relative to the repository/code location rather than relying on the current working directory.

--------------------------------------------------
A. Typed data structures
--------------------------------------------------

Create clear typed representations for the records.

You may use dataclasses and standard-library typing.

At minimum represent:

Request
SampleRequest
FinancialProfile
FinancialEvent
ExchangeRate
PaymentOption
Message
ImageRecord

Preserve the original fields needed by later phases.

Do not discard information simply because it is not currently used.

--------------------------------------------------
B. Parsing
--------------------------------------------------

Parse CSV values into appropriate Python types.

Examples:

- dates → datetime.date
- booleans → bool
- numeric amounts → Decimal where financial precision matters
- empty strings → None where appropriate
- identifiers → strings

For financial amounts, prefer Decimal over float to avoid monetary rounding errors.

Do not silently convert a missing financial amount to zero.

In particular:

financial_events.amount == blank

must remain explicitly missing/None.

Do NOT attempt to resolve blank amounts from images in this phase.

That belongs to the later evidence phase.

--------------------------------------------------
C. Validation
--------------------------------------------------

Validate the structure of every CSV against the actual discovered schema.

At minimum verify:

requests.csv contains:
- request_id
- user_id
- request_date
- request_type
- requested_amount
- desired_completion_date
- allows_partial_payment
- request_text

financial_profiles.csv contains:
- user_id
- home_currency
- current_available_balance
- minimum_balance_to_keep
- financial_priorities
- expense_categories_to_protect
- expense_categories_user_is_willing_to_reduce
- expense_categories_user_is_willing_to_stop
- payment_methods_user_will_consider
- max_installment_months

financial_events.csv contains:
- event_id
- user_id
- event_type
- description
- category
- direction
- amount
- currency
- event_date
- settlement_date
- status
- linked_event_id
- flexibility
- minimum_allowed_amount

exchange_rates.csv contains:
- rate_date
- from_currency
- to_currency
- rate

request_payment_options.csv contains:
- payment_option_id
- request_id
- payment_method
- payment_amount
- number_of_payments
- first_payment_date
- payment_frequency_days
- financing_fee
- total_payable_amount

messages.csv contains:
- message_id
- user_id
- request_id
- related_event_id
- sent_at
- source_type
- message_text

images.csv contains:
- image_id
- user_id
- request_id
- related_event_id

If the actual data differs from this in a meaningful way, stop and report it rather than silently changing the schema.

--------------------------------------------------
D. Build indexes
--------------------------------------------------

Create efficient lookup indexes for:

1. profiles_by_user_id
2. events_by_user_id
3. events_by_event_id
4. messages_by_user_id
5. messages_by_request_id
6. messages_by_related_event_id
7. images_by_image_id
8. images_by_user_id
9. images_by_request_id
10. images_by_related_event_id
11. payment_options_by_request_id
12. exchange_rates_by_date_and_currency_pair

The Phase 0 report confirmed:

- request_id → request/payment options
- user_id → profile/events
- related_event_id → messages/images to specific financial events
- event_id → financial event
- exchange rate key = rate_date + currency pair

Do NOT assume financial_events.csv has request_id.
It does NOT.

--------------------------------------------------
E. Data integrity checks
--------------------------------------------------

Add validation checks for known dataset properties.

Check at least:

- every request has exactly one matching profile
- every request has payment options
- event_id is unique
- payment_option_id is unique
- message_id is unique
- image_id is unique
- every image referenced by images.csv exists at:
  dataset/media/images/<image_id>.png
- every blank financial event amount has a matching image record
- no image record points to a nonexistent event when related_event_id is populated
- exchange-rate records have valid dates/currencies/rates
- all requests use supported home currencies
- no unexpected duplicate primary identifiers

Do not hardcode counts such as "there must be 250 requests" as the only validation.
Use the actual dataset structure.

You may additionally report the currently observed counts from Phase 0.

--------------------------------------------------
F. Access API
--------------------------------------------------

Expose a clean DataStore/DataRepository-style API.

For example:

get_request(request_id)
get_sample_request(request_id)
get_profile(user_id)
get_events_for_user(user_id)
get_event(event_id)
get_payment_options(request_id)
get_messages_for_user(user_id)
get_messages_for_request(request_id)
get_messages_for_event(event_id)
get_images_for_event(event_id)
get_images_for_request(request_id)
get_images_for_user(user_id)

Names may differ if a better design is found.

The important thing is that later modules should not need to know how CSV files are parsed.

==================================================
PART 2 — currency.py
==================================================

Create:

code/currency.py

This module must implement deterministic currency conversion using ONLY:

dataset/exchange_rates.csv

No live APIs.
No internet.
No market data.

--------------------------------------------------
A. Conversion requirements
--------------------------------------------------

The Phase 0 inspection found:

- supported currencies: INR, ZAR, IDR, USD, EUR
- exchange_rates.csv uses EUR/USD as source pivots
- foreign-currency financial events are rare
- rates must be matched by exact settlement date
- direct OR inverted rate lookup is required

Implement:

convert(amount, from_currency, to_currency, date)

using Decimal.

If:

from_currency == to_currency

return the original amount exactly.

Otherwise:

1. Look for exact-date direct rate:
   from_currency → to_currency

2. If unavailable, look for exact-date inverse rate:
   to_currency → from_currency

3. If inverse exists, invert it correctly.

4. If neither exists:
   raise a clear error.

DO NOT:
- use a nearby date
- use the latest rate
- interpolate
- use live FX
- silently use 1.0

--------------------------------------------------
B. Preserve monetary precision
--------------------------------------------------

Use Decimal for monetary calculations.

Avoid binary floating-point calculations such as:

float(amount) * float(rate)

when calculating financial values.

Define a clear policy for Decimal precision/rounding.

Do not arbitrarily round intermediate calculations.

Only round when required by the dataset/output contract.

--------------------------------------------------
C. Exchange-rate indexing
--------------------------------------------------

Build an efficient lookup:

(date, from_currency, to_currency) → rate

The currency module should not repeatedly scan the entire exchange_rates.csv.

--------------------------------------------------
D. Validation
--------------------------------------------------

Validate:

- currencies are supported
- rates are positive
- rate dates are valid
- direct/inverse lookup behaves correctly
- same-currency conversion works
- missing-rate errors are explicit

Add unit tests for:

1. same currency
2. direct conversion
3. inverse conversion
4. missing rate
5. Decimal precision
6. exact-date matching

Use actual exchange-rate rows from the dataset for tests.

==================================================
PART 3 — INTEGRATION SMOKE TEST
==================================================

Create a small deterministic smoke test.

It should:

1. Load the complete dataset using DataStore.
2. Print the number of records loaded from every file.
3. Load all 25 sample requests.
4. For several sample requests:
   - retrieve the request
   - retrieve its profile
   - retrieve its user's financial events
   - retrieve its payment options
   - retrieve relevant messages
   - retrieve relevant images
5. Demonstrate at least one blank-amount event and confirm:
   - its amount is still None
   - its linked image can be found
6. Demonstrate at least one foreign-currency event.
7. Convert that event into the user's home currency using the currency module.
8. Demonstrate both direct/inverse exchange-rate lookup if the actual dataset supports both.

Do not resolve the blank amount from the image yet.

Do not make any financial decision.

==================================================
PART 4 — MAIN.PY
==================================================

Update code/main.py only enough to perform the Phase 1 smoke test.

Running:

python3 code/main.py

must:

- load the data
- validate the data
- run the smoke test
- print a concise success summary
- exit successfully

Do NOT generate real predictions yet.

Do NOT overwrite the final output.csv with fake predictions.

==================================================
PART 5 — TESTING
==================================================

Create appropriate tests under code/ or another sensible test location.

Test:

- CSV parsing
- Decimal parsing
- date parsing
- boolean parsing
- missing values
- primary-key uniqueness
- foreign-key/index lookups
- image path validation
- currency conversion
- direct FX lookup
- inverse FX lookup
- missing FX rate handling

Run all tests.

Then run:

python3 code/main.py

==================================================
IMPORTANT DESIGN RULES
==================================================

1. Deterministic only.
2. No LLM.
3. No vision model.
4. No API calls.
5. No forecasting.
6. No affordability logic.
7. No payment-plan logic.
8. No modification to dataset/.
9. Do not hardcode solved sample outputs.
10. Do not infer financial behavior yet.
11. Do not treat blank amounts as zero.
12. Do not assume financial_events.csv has request_id.
13. Do not use floats for financial calculations where Decimal is appropriate.
14. Do not use live exchange rates.
15. Fail clearly when required data is inconsistent.

==================================================
DELIVERABLES
==================================================

After completing Phase 1, the repository should contain approximately:

code/
    main.py
    data_loader.py
    currency.py
    tests/...  (if appropriate)

Do not create financial_state.py, forecast.py, payment_plans.py,
decision_engine.py, verifier.py, evidence.py, or explanation.py yet
unless absolutely necessary for testing.

Report:

- files created
- files modified
- tests executed
- test results
- smoke-test results
- number of records loaded
- any data inconsistencies
- confirmation that dataset/ was not modified

STOP after Phase 1.

Do NOT automatically start Phase 2.