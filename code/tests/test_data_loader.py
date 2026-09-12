"""Tests for code/data_loader.py.

Two groups:

* ``RealDatasetTests`` -- integration tests against the actual dataset/
  directory (read-only). Skipped automatically if dataset/ is absent.
* ``SyntheticDatasetTests`` -- unit tests against a small, hand-built
  fixture dataset in a temp directory, used to exercise failure paths
  (missing columns, dangling references, duplicate ids, ...) that the
  real, already-clean dataset cannot exercise.
"""

import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_loader import (  # noqa: E402
    DataIntegrityError,
    DataStore,
    SchemaValidationError,
    default_dataset_dir,
    parse_bool,
    parse_date,
    parse_datetime_utc,
    parse_decimal,
    parse_optional_date,
    parse_optional_decimal,
    parse_optional_int,
    parse_pipe_list,
)

# A 1x1 transparent PNG, used as a placeholder image file in the fixture.
_TINY_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a4944415478da6360000002000155a5c0210000000049454e44ae426082"
)


# ---------------------------------------------------------------------------
# Scalar parser unit tests
# ---------------------------------------------------------------------------


class ScalarParsingTests(unittest.TestCase):
    def test_parse_decimal_uses_string_not_float(self):
        self.assertEqual(parse_decimal("58481.1"), Decimal("58481.1"))
        self.assertEqual(parse_decimal("0"), Decimal("0"))

    def test_parse_decimal_rejects_blank_and_garbage(self):
        with self.assertRaises(ValueError):
            parse_decimal("")
        with self.assertRaises(ValueError):
            parse_decimal("not-a-number")

    def test_parse_optional_decimal_blank_is_none_not_zero(self):
        self.assertIsNone(parse_optional_decimal(""))
        self.assertIsNone(parse_optional_decimal(None))
        self.assertEqual(parse_optional_decimal("12.5"), Decimal("12.5"))

    def test_parse_date(self):
        self.assertEqual(parse_date("2024-03-03"), date(2024, 3, 3))
        with self.assertRaises(ValueError):
            parse_date("03/03/2024")
        with self.assertRaises(ValueError):
            parse_date("")

    def test_parse_optional_date_blank_is_none(self):
        self.assertIsNone(parse_optional_date(""))
        self.assertEqual(parse_optional_date("2024-03-03"), date(2024, 3, 3))

    def test_parse_datetime_utc(self):
        dt = parse_datetime_utc("2025-07-29T09:30:00Z")
        self.assertEqual(dt, datetime(2025, 7, 29, 9, 30, 0, tzinfo=timezone.utc))

    def test_parse_bool(self):
        self.assertIs(parse_bool("true"), True)
        self.assertIs(parse_bool("false"), False)
        self.assertIs(parse_bool("True"), True)
        self.assertIs(parse_bool("FALSE"), False)
        with self.assertRaises(ValueError):
            parse_bool("yes")
        with self.assertRaises(ValueError):
            parse_bool("")

    def test_parse_optional_int_blank_is_none(self):
        self.assertIsNone(parse_optional_int(""))
        self.assertEqual(parse_optional_int("7"), 7)

    def test_parse_pipe_list(self):
        self.assertEqual(parse_pipe_list("education|debt_repayment"), ["education", "debt_repayment"])
        self.assertEqual(parse_pipe_list(""), [])
        self.assertEqual(parse_pipe_list(None), [])
        self.assertEqual(parse_pipe_list("dining"), ["dining"])


# ---------------------------------------------------------------------------
# Real-dataset integration tests
# ---------------------------------------------------------------------------


class RealDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not default_dataset_dir().exists():
            raise unittest.SkipTest("dataset/ not found; skipping integration tests")
        cls.store = DataStore.load(strict=True)

    def test_all_files_loaded_with_nonzero_rows(self):
        summary = self.store.summary()
        for name, count in summary.items():
            self.assertGreater(count, 0, f"{name} loaded zero rows")

    def test_integrity_report_has_no_errors_on_clean_dataset(self):
        self.assertTrue(self.store.integrity_report.ok, self.store.integrity_report.errors)

    def test_blank_amount_events_stay_none_and_have_an_image(self):
        blanks = [e for e in self.store.events if e.amount is None]
        self.assertGreater(len(blanks), 0)
        for ev in blanks:
            self.assertIsNone(ev.amount)
            images = self.store.get_images_for_event(ev.event_id)
            self.assertTrue(images, f"{ev.event_id} has no linked image")
            self.assertTrue(images[0].image_path.exists())

    def test_profile_with_blank_max_installment_months_is_none(self):
        profile = self.store.get_profile("user_01")
        self.assertIsNotNone(profile)
        self.assertIsNone(profile.max_installment_months)

    def test_events_by_user_id_index_is_consistent(self):
        events = self.store.get_events_for_user("user_01")
        self.assertGreater(len(events), 0)
        for ev in events:
            self.assertEqual(ev.user_id, "user_01")

    def test_get_event_by_id(self):
        any_event = self.store.events[0]
        fetched = self.store.get_event(any_event.event_id)
        self.assertIs(fetched, any_event)
        self.assertIsNone(self.store.get_event("event_does_not_exist"))

    def test_every_request_has_payment_options(self):
        for r in list(self.store.requests) + list(self.store.sample_requests):
            options = self.store.get_payment_options(r.request_id)
            self.assertTrue(options, f"{r.request_id} has no payment options")
            self.assertIn(len(options), (2, 3, 4))

    def test_request_and_user_id_are_index_aligned(self):
        # Phase 0 found request_N always belongs to user_N.
        sample = self.store.get_sample_request("request_01")
        self.assertIsNotNone(sample)
        self.assertEqual(sample.user_id, "user_01")

    def test_financial_events_have_no_request_id_field(self):
        # implementation/phase1.md is explicit that this column does not
        # exist; guard against a future dataset change silently adding it
        # and code elsewhere wrongly assuming its absence.
        from data_loader import EVENT_COLUMNS
        self.assertNotIn("request_id", EVENT_COLUMNS)

    def test_exchange_rate_index_matches_row_count_or_reports_duplicates(self):
        unique_keys = {(r.rate_date, r.from_currency, r.to_currency) for r in self.store.exchange_rates}
        self.assertEqual(len(unique_keys), len(self.store.exchange_rates))
        self.assertEqual(len(self.store.exchange_rates_by_date_and_currency_pair), len(unique_keys))

    def test_message_and_image_lookup_by_related_event(self):
        blank = next(e for e in self.store.events if e.amount is None)
        images = self.store.get_images_for_event(blank.event_id)
        self.assertTrue(images)
        # every image's own related_event_id must round-trip
        for img in images:
            self.assertEqual(img.related_event_id, blank.event_id)


# ---------------------------------------------------------------------------
# Synthetic fixture dataset -- used to exercise failure paths
# ---------------------------------------------------------------------------


def _write_csv(path: Path, header: str, rows: list) -> None:
    lines = [header] + rows
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fixture_dataset(
    root: Path,
    *,
    drop_request_column: bool = False,
    delete_image_file: bool = False,
    omit_image_row: bool = False,
    duplicate_event_id: bool = False,
    omit_payment_options_for_request: bool = False,
    omit_profile_for_user: bool = False,
) -> Path:
    """Write a minimal, otherwise-valid dataset into ``root`` and return it.

    Each keyword flag introduces exactly one specific integrity/schema
    problem, so each negative test can assert on a single failure cause.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "media" / "images").mkdir(parents=True, exist_ok=True)

    request_columns = [
        "request_id", "user_id", "request_date", "request_type",
        "requested_amount", "desired_completion_date", "allows_partial_payment",
        "request_text",
    ]
    if drop_request_column:
        request_columns.remove("request_text")
    _write_csv(
        root / "requests.csv",
        ",".join(request_columns),
        [",".join(
            v for col, v in zip(
                ["request_id", "user_id", "request_date", "request_type",
                 "requested_amount", "desired_completion_date",
                 "allows_partial_payment", "request_text"],
                ["request_1", "user_1", "2024-01-01", "purchase",
                 "100", "2024-02-01", "true", "\"Test request\""],
            ) if col in request_columns
        )],
    )

    _write_csv(
        root / "sample_requests.csv",
        "request_id,user_id,request_date,request_type,requested_amount,"
        "desired_completion_date,allows_partial_payment,request_text,"
        "amount_safe_to_pay,affordability_status,recommended_payment_method,"
        "payment_plan,earliest_date_for_full_payment,spending_changes_needed,"
        "decision_explanation",
        [
            "sample_1,user_2,2024-01-01,purchase,50,2024-02-01,true,"
            "\"Sample request\",50,affordable_now,full_payment,"
            "2024-01-01:50,2024-01-01,none,\"Pay in full today\""
        ],
    )

    profile_rows = [
        "user_1,USD,1000,100,education,rent,,,full_payment,",
        "user_2,USD,1000,100,education,rent,,,full_payment,",
    ]
    if omit_profile_for_user == "user_1":
        profile_rows = [r for r in profile_rows if not r.startswith("user_1,")]
    _write_csv(
        root / "financial_profiles.csv",
        "user_id,home_currency,current_available_balance,minimum_balance_to_keep,"
        "financial_priorities,expense_categories_to_protect,"
        "expense_categories_user_is_willing_to_reduce,"
        "expense_categories_user_is_willing_to_stop,payment_methods_user_will_consider,"
        "max_installment_months",
        profile_rows,
    )

    event_rows = [
        "event_1,user_1,expense,Rent,rent,debit,500,USD,2024-01-01,2024-01-01,settled,,fixed,",
        "event_2,user_1,expense,Bill with unknown amount,utilities,debit,,EUR,2024-01-02,2024-01-02,settled,,fixed,",
    ]
    if duplicate_event_id:
        event_rows.append(
            "event_1,user_1,expense,Duplicate id,rent,debit,999,USD,2024-01-03,2024-01-03,settled,,fixed,"
        )
    _write_csv(
        root / "financial_events.csv",
        "event_id,user_id,event_type,description,category,direction,amount,currency,"
        "event_date,settlement_date,status,linked_event_id,flexibility,minimum_allowed_amount",
        event_rows,
    )

    _write_csv(
        root / "exchange_rates.csv",
        "rate_date,from_currency,to_currency,rate",
        ["2024-01-02,EUR,USD,1.1"],
    )

    po_rows = [
        "payment_option_01,request_1,full_payment,100,1,2024-01-01,,0,100",
        "payment_option_02,sample_1,full_payment,50,1,2024-01-01,,0,50",
    ]
    if omit_payment_options_for_request == "request_1":
        po_rows = [r for r in po_rows if not r.startswith("payment_option_01,")]
    _write_csv(
        root / "request_payment_options.csv",
        "payment_option_id,request_id,payment_method,payment_amount,number_of_payments,"
        "first_payment_date,payment_frequency_days,financing_fee,total_payable_amount",
        po_rows,
    )

    _write_csv(
        root / "messages.csv",
        "message_id,user_id,request_id,related_event_id,sent_at,source_type,message_text",
        ["message_1,user_1,request_1,,2024-01-01T09:00:00Z,bank,\"A note\""],
    )

    image_rows = ["image_1,user_1,request_1,event_2"]
    if omit_image_row:
        image_rows = []
    _write_csv(
        root / "images.csv",
        "image_id,user_id,request_id,related_event_id",
        image_rows,
    )

    image_path = root / "media" / "images" / "image_1.png"
    if not delete_image_file:
        image_path.write_bytes(_TINY_PNG_BYTES)

    return root


class SyntheticDatasetTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="buy_or_wait_fixture_")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.root = Path(self._tmp)

    def test_happy_path_fixture_loads_cleanly(self):
        build_fixture_dataset(self.root)
        store = DataStore.load(self.root, strict=True)
        self.assertTrue(store.integrity_report.ok)
        self.assertEqual(len(store.requests), 1)
        self.assertEqual(len(store.sample_requests), 1)
        event2 = store.get_event("event_2")
        self.assertIsNone(event2.amount)

    def test_missing_required_column_raises_schema_error(self):
        build_fixture_dataset(self.root, drop_request_column=True)
        with self.assertRaises(SchemaValidationError):
            DataStore.load(self.root, strict=True)

    def test_missing_image_file_on_disk_is_an_integrity_error(self):
        build_fixture_dataset(self.root, delete_image_file=True)
        with self.assertRaises(DataIntegrityError) as ctx:
            DataStore.load(self.root, strict=True)
        self.assertIn("image file not found", str(ctx.exception))

    def test_blank_amount_event_without_image_row_is_an_integrity_error(self):
        build_fixture_dataset(self.root, omit_image_row=True)
        with self.assertRaises(DataIntegrityError) as ctx:
            DataStore.load(self.root, strict=True)
        self.assertIn("no images.csv row references it", str(ctx.exception))

    def test_duplicate_event_id_is_an_integrity_error(self):
        build_fixture_dataset(self.root, duplicate_event_id=True)
        with self.assertRaises(DataIntegrityError) as ctx:
            DataStore.load(self.root, strict=True)
        self.assertIn("Duplicate event_id", str(ctx.exception))

    def test_request_without_payment_options_is_an_integrity_error(self):
        build_fixture_dataset(self.root, omit_payment_options_for_request="request_1")
        with self.assertRaises(DataIntegrityError) as ctx:
            DataStore.load(self.root, strict=True)
        self.assertIn("no payment options found", str(ctx.exception))

    def test_request_without_profile_is_an_integrity_error(self):
        build_fixture_dataset(self.root, omit_profile_for_user="user_1")
        with self.assertRaises(DataIntegrityError) as ctx:
            DataStore.load(self.root, strict=True)
        self.assertIn("no financial profile found", str(ctx.exception))

    def test_strict_false_returns_store_with_errors_instead_of_raising(self):
        build_fixture_dataset(self.root, delete_image_file=True)
        store = DataStore.load(self.root, strict=False)
        self.assertFalse(store.integrity_report.ok)
        self.assertTrue(any("image file not found" in e for e in store.integrity_report.errors))

    def test_dataset_directory_not_found_raises_data_error(self):
        from data_loader import DataError
        with self.assertRaises(DataError):
            DataStore.load(self.root / "does_not_exist", strict=True)


if __name__ == "__main__":
    unittest.main()
