"""Tests for code/currency.py.

Rate values used below are copied verbatim from dataset/exchange_rates.csv
(all three rows dated 2023-10-15), per implementation/phase1.md's
instruction to test against actual exchange-rate rows rather than
fabricated ones.
"""

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from currency import (  # noqa: E402
    CurrencyConversionError,
    CurrencyConverter,
    UnsupportedCurrencyError,
)
from data_loader import DataStore, ExchangeRate, default_dataset_dir  # noqa: E402

# Real rows from dataset/exchange_rates.csv, rate_date=2023-10-15.
EUR_TO_ZAR_20231015 = ExchangeRate(date(2023, 10, 15), "EUR", "ZAR", Decimal("20"))
USD_TO_EUR_20231015 = ExchangeRate(date(2023, 10, 15), "USD", "EUR", Decimal("0.92"))
USD_TO_IDR_20231015 = ExchangeRate(date(2023, 10, 15), "USD", "IDR", Decimal("15833.33"))

REAL_ROWS = [EUR_TO_ZAR_20231015, USD_TO_EUR_20231015, USD_TO_IDR_20231015]


class CurrencyConverterUnitTests(unittest.TestCase):
    def setUp(self):
        self.converter = CurrencyConverter(REAL_ROWS)

    def test_same_currency_returns_amount_unchanged_no_lookup(self):
        amount = Decimal("12345.67")
        result = self.converter.convert(amount, "USD", "USD", date(2023, 10, 15))
        self.assertEqual(result, amount)
        # Same-currency same-day should not require any rate row at all.
        empty_converter = CurrencyConverter([])
        result2 = empty_converter.convert(amount, "EUR", "EUR", date(1999, 1, 1))
        self.assertEqual(result2, amount)

    def test_direct_conversion_uses_exact_rate_row(self):
        rate = self.converter.get_rate(date(2023, 10, 15), "USD", "IDR")
        self.assertEqual(rate, Decimal("15833.33"))
        converted = self.converter.convert(Decimal("1"), "USD", "IDR", date(2023, 10, 15))
        self.assertEqual(converted, Decimal("15833.33"))

    def test_inverse_conversion_is_computed_from_the_direct_row(self):
        # There is no IDR->USD row, only USD->IDR. The converter must invert it.
        expected_rate = Decimal("1") / Decimal("15833.33")
        rate = self.converter.get_rate(date(2023, 10, 15), "IDR", "USD")
        self.assertEqual(rate, expected_rate)
        converted = self.converter.convert(Decimal("15833.33"), "IDR", "USD", date(2023, 10, 15))
        self.assertEqual(converted, Decimal("15833.33") * expected_rate)

    def test_missing_rate_raises_clear_error_no_fallback(self):
        # ZAR never appears as a from_currency in the dataset, and there is
        # no ZAR<->IDR row at all (direct or inverse) on this date.
        with self.assertRaises(CurrencyConversionError):
            self.converter.get_rate(date(2023, 10, 15), "ZAR", "IDR")

    def test_decimal_precision_is_exact_no_float_drift(self):
        amount = Decimal("1801.23")
        converted = self.converter.convert(amount, "USD", "EUR", date(2023, 10, 15))
        # Exact Decimal multiplication -- must match exactly, not "close to".
        self.assertEqual(converted, Decimal("1801.23") * Decimal("0.92"))
        self.assertEqual(converted, Decimal("1657.1316"))

    def test_exact_date_matching_no_nearby_date_fallback(self):
        # A rate exists on 2023-10-15 but not on the very next day.
        self.converter.get_rate(date(2023, 10, 15), "USD", "EUR")  # sanity: this one works
        with self.assertRaises(CurrencyConversionError):
            self.converter.get_rate(date(2023, 10, 16), "USD", "EUR")

    def test_unsupported_currency_is_rejected(self):
        with self.assertRaises(UnsupportedCurrencyError):
            self.converter.get_rate(date(2023, 10, 15), "ZZZ", "USD")
        with self.assertRaises(UnsupportedCurrencyError):
            self.converter.get_rate(date(2023, 10, 15), "USD", "ZZZ")

    def test_convert_rejects_non_decimal_amount(self):
        with self.assertRaises(TypeError):
            self.converter.convert(1.5, "USD", "USD", date(2023, 10, 15))  # float, not Decimal


class CurrencyConverterIntegrationTest(unittest.TestCase):
    """End-to-end: load the real dataset and convert a real foreign-currency event."""

    @classmethod
    def setUpClass(cls):
        if not default_dataset_dir().exists():
            raise unittest.SkipTest("dataset/ not found; skipping integration test")
        cls.store = DataStore.load(strict=True)
        cls.converter = CurrencyConverter.from_data_store(cls.store)

    def test_convert_a_real_foreign_currency_event(self):
        foreign = None
        for ev in self.store.events:
            profile = self.store.get_profile(ev.user_id)
            if profile and ev.currency != profile.home_currency and ev.amount is not None:
                foreign = (ev, profile)
                break
        self.assertIsNotNone(foreign, "expected at least one foreign-currency event in the dataset")
        ev, profile = foreign
        on_date = ev.settlement_date or ev.event_date
        rate = self.converter.get_rate(on_date, ev.currency, profile.home_currency)
        converted = self.converter.convert(ev.amount, ev.currency, profile.home_currency, on_date)
        self.assertEqual(converted, ev.amount * rate)


if __name__ == "__main__":
    unittest.main()
