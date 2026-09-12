# Phase 1 Report — Data Loader + Currency Layer

**Scope (per `implementation/phase1.md`):** `code/data_loader.py`, `code/currency.py`, a Phase-1-only `code/main.py` smoke test, and tests. No financial-state reconstruction, recurrence detection, forecasting, affordability logic, payment-plan generation, spending-change optimization, plan ranking, LLM calls, vision calls, or decision explanations were implemented — all deferred to later phases as instructed.

---

## Files Created

| File | Purpose |
|---|---|
| `code/data_loader.py` | Typed dataclasses (`Request`, `SampleRequest`, `FinancialProfile`, `FinancialEvent`, `ExchangeRate`, `PaymentOption`, `Message`, `ImageRecord`); stdlib-`csv` loaders with schema validation; the `DataStore` class with all 12 requested indexes plus the requested access API; `IntegrityReport`-based data-integrity checks; `DataError` / `SchemaValidationError` / `DataIntegrityError` exception hierarchy. |
| `code/currency.py` | `CurrencyConverter` — exact-date direct-or-inverse rate lookup and `Decimal`-only conversion against `dataset/exchange_rates.csv`; `CurrencyError` / `UnsupportedCurrencyError` / `CurrencyConversionError` exceptions. No live rates, no interpolation, no nearest-date fallback. |
| `code/tests/test_data_loader.py` | Scalar-parser unit tests, real-dataset integration tests, and a synthetic-fixture suite (built in a temp dir) that exercises schema/integrity failure paths the real, already-clean dataset can't. |
| `code/tests/test_currency.py` | Unit tests against real rate rows copied from `dataset/exchange_rates.csv` (`2023-10-15` EUR→ZAR, USD→EUR, USD→IDR), plus one integration test that converts a real foreign-currency event end-to-end. |
| `eval/phase1_report.md` | This report. |

`.gitignore` was also extended with `__pycache__/` and `*.pyc` (pure repo hygiene from running the tests; unrelated to dataset or output).

## Files Modified

| File | Change |
|---|---|
| `code/main.py` | Was an empty 0-byte stub. Now loads the dataset via `DataStore.load(strict=True)`, prints record counts and any warnings, traces 4 sample requests end-to-end through profile/events/payment-options/messages/images, demonstrates the blank-amount-event → image link (without resolving the amount), demonstrates one real foreign-currency conversion, demonstrates a direct **and** an inverse exchange-rate lookup, and prints `PHASE 1 SMOKE TEST: SUCCESS`. It makes no affordability decision and does not write `output.csv`. |

No file under `dataset/` was created, modified, or deleted. `output.csv` (root) was not created; `dataset/output.csv` (the blank template) was not touched.

---

## Tests Executed

Run with:

```bash
python -m unittest discover -s code/tests -p "test_*.py" -v
```

**Result: 38 / 38 passed, 0 failed, 0 errors, 0 skipped** (the dataset was present, so the dataset-dependent integration tests ran rather than being skipped).

Coverage by category:

- **CSV/scalar parsing** (`ScalarParsingTests`, 9 tests): `Decimal` parsing (string-based, not `float`-based), blank→`None` handling, date parsing (`YYYY-MM-DD`), datetime parsing (`...Z` → UTC), boolean parsing (`true`/`false`, case-insensitive, rejects anything else), pipe-delimited list parsing.
- **Real-dataset integration** (`RealDatasetTests`, 10 tests): every file loads with the expected non-zero row counts; `validate_integrity()` reports zero errors on the actual dataset; all 16 blank-`amount` events stay `None` and each resolves to an existing image file; a known blank `max_installment_months` (`user_01`) parses to `None`; `events_by_user_id` / `get_event` / `get_payment_options` indexes are internally consistent; every one of the 275 requests (250 + 25 samples) has 2–4 payment options; `request_N` ↔ `user_N` alignment holds; `financial_events.csv` genuinely has no `request_id` column; the exchange-rate index has no silently-dropped duplicate keys.
- **Synthetic-fixture failure paths** (`SyntheticDatasetTests`, 9 tests, using a hand-built temp dataset since the real data has no defects to trigger these): a clean fixture loads without error; a missing required CSV column raises `SchemaValidationError`; a missing image file on disk, a blank-amount event with no `images.csv` row, a duplicate `event_id`, a request with no payment options, and a request with no matching profile each raise `DataIntegrityError` with a message naming the specific cause; `strict=False` returns a store with the same problems recorded in `integrity_report` instead of raising; a nonexistent dataset directory raises `DataError`.
- **Currency unit + integration** (`test_currency.py`, 9 tests): same-currency short-circuit (no rate lookup needed), direct conversion, inverse conversion (computed from the one-directional row, since the table only stores EUR/USD as `from_currency`), missing-rate rejection (no pair, no fallback), exact `Decimal` arithmetic (`1801.23 × 0.92 = 1657.1316`, asserted exactly — not "close to"), exact-date matching (a rate one day off from an existing `rate_date` is *not* reused), unsupported-currency rejection, `TypeError` on a non-`Decimal` amount, and one real-data round trip (a real foreign-currency salary event converted to its user's home currency).

---

## Smoke-Test Results (`python code/main.py`)

Exit code: **0**. Full output:

```
Buy or Wait? -- Phase 1 data-loading smoke test
Repository root : C:\main_code\hackerrank-orchestrate-september26

Record counts
-------------
  requests         250
  sample_requests  25
  profiles         275
  events           25342
  exchange_rates   134
  payment_options  790
  messages         215
  images           16

Sample request traces
---------------------
  request_01 (user user_01, purchase): 103 events, 4 payment options, solved = affordable_now / full_payment
  request_02 (user user_02, travel):    82 events, 3 payment options, solved = affordable_with_plan / installments
  request_06 (user user_06, investment): 119 events, 3 payment options, solved = affordable_with_plan / full_payment
  request_19 (user user_19, purchase):   79 events, 3 payment options, solved = affordable_with_plan / partial_payment

Blank-amount event -> image resolution (not resolved yet)
  event event_253 ('August 2019 net salary'): amount is None -> True
  linked image_id=image_01, exists=True
  (amount extraction from the image is out of scope for Phase 1)

Foreign-currency event -> home-currency conversion
  event event_2167 ('International employer payroll'): 1800 USD on 2023-10-15 -> 28499994.00 IDR
  rate used (USD->IDR on 2023-10-15): 15833.33

Direct vs. inverse exchange-rate lookup
  direct  EUR->ZAR on 2023-10-15 = 20
  inverse ZAR->EUR on 2023-10-15 = 0.05
  direct * inverse ~= 1 (sanity check): 1.0000000000
  unsupported currency correctly rejected: UnsupportedCurrencyError: unsupported currency 'ZZZ'; ...

PHASE 1 SMOKE TEST: SUCCESS
(no affordability decisions were made; output.csv was not touched)
```

No loader warnings and no integrity warnings/errors were printed, confirming the dataset is internally clean against every check `data_loader.py` runs.

---

## Number of Records Loaded

| File | Rows loaded | Matches Phase 0 recon? |
|---|---|---|
| `requests.csv` | 250 | Yes |
| `sample_requests.csv` | 25 | Yes |
| `financial_profiles.csv` | 275 | Yes |
| `financial_events.csv` | 25,342 | Yes |
| `exchange_rates.csv` | 134 | Yes |
| `request_payment_options.csv` | 790 | Yes |
| `messages.csv` | 215 | Yes |
| `images.csv` | 16 | Yes |

`DataStore.count_drift_notes()` (an informational-only comparison against the Phase 0 counts, never used as a validation gate) reported **no drift** — the dataset is unchanged since Phase 0.

---

## Data Inconsistencies Found

**None.** `store.integrity_report.ok` is `True` and `store.integrity_report.errors` is empty on the real dataset, across every check implemented:

- every request (250 + 25 samples) has exactly one matching profile,
- every request has 2–4 payment options and every payment option belongs to a real request,
- `event_id`, `payment_option_id`, `message_id`, `image_id`, profile `user_id`, and `request_id` (within each of `requests.csv`/`sample_requests.csv`, and across both) are all unique,
- every `images.csv` row's file exists at `dataset/media/images/<image_id>.png`,
- every blank-`amount` event has a corresponding `images.csv` row, and no image row points at a nonexistent event,
- every exchange-rate row has a positive rate and a supported currency pair,
- every profile and every event uses one of the five supported currencies (`INR`, `ZAR`, `IDR`, `USD`, `EUR`).

Two categories of **non-fatal loader warnings** are supported (surfaced via `store.warnings` and `store.integrity_report.warnings`) but did not fire on this dataset: unexpected extra CSV columns, and messages whose `request_id`/`related_event_id` don't resolve to a known request/event. Both are implemented defensively for a dataset that might change later, not because the current data needs them.

---

## Confirmation: `dataset/` Was Not Modified

Verified with `git status --porcelain` and `git diff --stat -- dataset/` after all implementation and test runs: **zero changes reported under `dataset/`**. The only new/changed paths in the whole repository are `.gitignore`, `code/main.py` (modified), `code/data_loader.py`, `code/currency.py`, `code/tests/`, and `eval/phase1_report.md`.

---

## Design Notes Carried Forward

- **Paths are code-relative, not cwd-relative**: `default_dataset_dir()` resolves from `Path(__file__)`, so `python3 code/main.py` behaves identically regardless of the invoking directory.
- **`financial_events.csv` has no `request_id`** — enforced by a test (`test_financial_events_have_no_request_id_field`) so a later phase can't silently assume otherwise.
- **Blank amounts are never coerced to zero** anywhere in the loader; `FinancialEvent.amount` is `Optional[Decimal]` and stays `None` until a later evidence phase resolves it from the linked image.
- **All money uses `Decimal`**, constructed from the original string (never via `float`), including in `currency.py`'s conversion arithmetic.
- **Currency conversion is exact-date only** — direct row, then inverted row, then a clear `CurrencyConversionError`. No nearest-date, no interpolation, no live rates, matching the Phase 0 finding that all 140 real foreign-currency events resolve this way.
- **Fail loudly, not softly**: a missing/renamed column raises `SchemaValidationError` immediately (per phase1.md: "stop and report it rather than silently changing the schema"); cross-file problems are collected into one `IntegrityReport` and raised as a single `DataIntegrityError` (or returned for inspection with `strict=False`).

## Recommended Next Phase

**Phase 2: `financial_state.py`** — recurrence detection and conflict resolution over `DataStore.events`, built strictly on top of the `DataStore`/`CurrencyConverter` API delivered here, with no forecasting or affordability logic yet (per the Phase 0 report's §11 recommended order). No evidence/LLM work should start before this, since evidence facts need a clean ledger to reconcile against.

---

Stopping here, per `implementation/phase1.md`. Phase 2 was not started automatically.
