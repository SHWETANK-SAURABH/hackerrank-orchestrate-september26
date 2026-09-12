"""Tests for code/output_pipeline.py: serialization, the deterministic
explanation template, independent row validation, and an end-to-end run
against the real dataset.
"""

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from currency import CurrencyConverter  # noqa: E402
from data_loader import DataStore, default_dataset_dir  # noqa: E402
from decision_engine import make_decision  # noqa: E402
from financial_state import build_financial_state  # noqa: E402
from payment_plans import PaymentPlan  # noqa: E402
import output_pipeline as op  # noqa: E402

D = date.fromisoformat


class SerializationTests(unittest.TestCase):
    def test_format_money_no_scientific_notation_integer(self):
        self.assertEqual(op.format_money(Decimal("25256")), "25256")

    def test_format_money_preserves_given_precision(self):
        self.assertEqual(op.format_money(Decimal("620.40")), "620.40")
        self.assertEqual(op.format_money(Decimal("17229139.2")), "17229139.2")

    def test_format_money_large_value_stays_fixed_point(self):
        self.assertEqual(op.format_money(Decimal("123456789012.34")), "123456789012.34")
        self.assertNotIn("E", op.format_money(Decimal("123456789012.34")))

    def test_format_date_none_is_empty_string(self):
        self.assertEqual(op.format_date(None), "")

    def test_format_date_present(self):
        self.assertEqual(op.format_date(D("2026-01-15")), "2026-01-15")

    def test_format_payment_plan_none_when_no_plan(self):
        self.assertEqual(op.format_payment_plan(None), "none")

    def test_format_payment_plan_single_leg(self):
        plan = PaymentPlan(
            method="full_payment", payment_dates=(D("2024-03-03"),), payment_amounts=(Decimal("25256"),),
            number_of_payments=1, total_payable=Decimal("25256"), financing_fee=Decimal(0),
            full_payment_date=D("2024-03-03"),
        )
        self.assertEqual(op.format_payment_plan(plan), "2024-03-03:25256")

    def test_format_payment_plan_multi_leg_chronological(self):
        plan = PaymentPlan(
            method="installments",
            payment_dates=(D("2025-08-08"), D("2025-09-07"), D("2025-10-07")),
            payment_amounts=(Decimal("100"), Decimal("100"), Decimal("100")),
            number_of_payments=3, total_payable=Decimal("300"), financing_fee=Decimal("0"),
            full_payment_date=D("2025-10-07"), source_payment_option_id="payment_option_1",
        )
        self.assertEqual(op.format_payment_plan(plan), "2025-08-08:100|2025-09-07:100|2025-10-07:100")

    def test_format_spending_changes_none(self):
        self.assertEqual(op.format_spending_changes(()), "none")

    def test_format_spending_changes_joined_with_pipe(self):
        self.assertEqual(
            op.format_spending_changes(("stop:event_14", "reduce_to:event_21:100")),
            "stop:event_14|reduce_to:event_21:100",
        )

    def test_parse_plan_string_round_trip(self):
        text = "2024-09-04:30153.59|2024-09-15:9506.41"
        parsed = op._parse_plan_string(text)
        self.assertEqual(parsed, [(D("2024-09-04"), Decimal("30153.59")), (D("2024-09-15"), Decimal("9506.41"))])

    def test_parse_plan_string_none(self):
        self.assertEqual(op._parse_plan_string("none"), [])

    def test_parse_spending_changes_string_round_trip(self):
        self.assertEqual(
            op._parse_spending_changes_string("stop:event_14|reduce_to:event_21:100"),
            ["stop:event_14", "reduce_to:event_21:100"],
        )


class ExplanationTests(unittest.TestCase):
    def _decision_and_state(self, rid="request_01"):
        if not default_dataset_dir().exists():
            self.skipTest("dataset/ not found")
        store = DataStore.load(strict=True)
        converter = CurrencyConverter.from_data_store(store)
        sample = store.get_sample_request(rid)
        state = build_financial_state(store, converter, sample.user_id)
        options = store.get_payment_options(sample.request_id)
        decision = make_decision(sample, state, options)
        return sample, state, decision

    def test_explanation_never_claims_a_payment_was_made(self):
        request, state, decision = self._decision_and_state("request_01")
        text = op.build_decision_explanation(request, state, decision, [])
        for phrase in ("was paid", "has been paid", "payment was made", "we paid"):
            self.assertNotIn(phrase, text.lower())

    def test_explanation_never_claims_live_data_was_consulted(self):
        request, state, decision = self._decision_and_state("request_01")
        text = op.build_decision_explanation(request, state, decision, [])
        for phrase in ("bank was contacted", "live market", "checked the account", "real-time"):
            self.assertNotIn(phrase, text.lower())

    def test_explanation_does_not_mention_evidence_when_not_material(self):
        request, state, decision = self._decision_and_state("request_01")
        text = op.build_decision_explanation(request, state, decision, [])
        self.assertNotIn("evidence update", text)

    def test_explanation_mentions_evidence_only_when_material_list_nonempty(self):
        request, state, decision = self._decision_and_state("request_01")
        from evidence import EvidenceFact, FactType, EvidenceResolution
        fake_fact = EvidenceFact(FactType.INCOME_CONFIRMATION, request.user_id, None, None, "message", "message_x",
                                  Decimal(100), "USD", None, None, "high", "text")
        resolution = EvidenceResolution("test", fake_fact, None, "100")
        text = op.build_decision_explanation(request, state, decision, [resolution])
        self.assertIn("evidence update", text)

    def test_explanation_mentions_requested_and_safe_amounts(self):
        request, state, decision = self._decision_and_state("request_01")
        text = op.build_decision_explanation(request, state, decision, [])
        self.assertIn(op.format_money(request.requested_amount), text)


class ValidateOutputRowTests(unittest.TestCase):
    def setUp(self):
        if not default_dataset_dir().exists():
            self.skipTest("dataset/ not found")
        self.store = DataStore.load(strict=True)
        self.converter = CurrencyConverter.from_data_store(self.store)

    def _row_and_context(self, rid):
        sample = self.store.get_sample_request(rid) or self.store.get_request(rid)
        state = build_financial_state(self.store, self.converter, sample.user_id)
        options = self.store.get_payment_options(sample.request_id)
        options_by_id = {o.payment_option_id: o for o in options}
        decision = make_decision(sample, state, options)
        row = op.build_output_row(sample, state, decision, [])
        return row, sample, state, decision, options_by_id

    def test_a_correctly_generated_row_has_zero_issues(self):
        row, request, state, decision, options_by_id = self._row_and_context("request_01")
        issues = op.validate_output_row(row, request, state, decision, options_by_id)
        self.assertEqual(issues, [])

    def test_tampered_amount_out_of_bounds_is_caught(self):
        row, request, state, decision, options_by_id = self._row_and_context("request_01")
        from dataclasses import replace
        bad_row = replace(row, amount_safe_to_pay=request.requested_amount + Decimal(1))
        issues = op.validate_output_row(bad_row, request, state, decision, options_by_id)
        self.assertTrue(any(cat == "numeric" for cat, _ in issues))

    def test_tampered_status_method_combo_is_caught(self):
        row, request, state, decision, options_by_id = self._row_and_context("request_01")
        from dataclasses import replace
        bad_row = replace(row, affordability_status="not_affordable")  # method stays full_payment
        issues = op.validate_output_row(bad_row, request, state, decision, options_by_id)
        self.assertTrue(any(cat == "schema" for cat, _ in issues))

    def test_tampered_payment_plan_string_desyncs_from_decision(self):
        row, request, state, decision, options_by_id = self._row_and_context("request_01")
        from dataclasses import replace
        bad_row = replace(row, payment_plan=f"{request.request_date}:1")
        issues = op.validate_output_row(bad_row, request, state, decision, options_by_id)
        self.assertTrue(any(cat == "payment_plan" for cat, _ in issues))

    def test_tampered_spending_change_syntax_is_caught(self):
        row, request, state, decision, options_by_id = self._row_and_context("request_01")
        from dataclasses import replace
        bad_row = replace(row, spending_changes_needed="delete_everything:event_1")
        issues = op.validate_output_row(bad_row, request, state, decision, options_by_id)
        self.assertTrue(any(cat == "spending_change" for cat, _ in issues))

    def test_all_25_samples_generate_zero_validation_issues(self):
        for sample in self.store.sample_requests:
            row, request, state, decision, options_by_id = self._row_and_context(sample.request_id)
            issues = op.validate_output_row(row, request, state, decision, options_by_id)
            self.assertEqual(issues, [], f"{sample.request_id}: {issues}")


class EndToEndPipelineTests(unittest.TestCase):
    def setUp(self):
        if not default_dataset_dir().exists():
            self.skipTest("dataset/ not found")
        self.store = DataStore.load(strict=True)
        self.converter = CurrencyConverter.from_data_store(self.store)

    def test_pipeline_produces_exactly_250_rows_with_zero_validation_issues(self):
        rows, diagnostics, issues = op.run_production_pipeline(self.store, self.converter)
        self.assertEqual(len(rows), 250)
        self.assertEqual(diagnostics.total_requests, 250)
        self.assertEqual(issues, {}, f"{len(issues)} request(s) failed independent validation: {list(issues)[:5]}")

    def test_pipeline_is_deterministic_across_two_runs(self):
        rows1, _, _ = op.run_production_pipeline(self.store, self.converter)
        rows2, _, _ = op.run_production_pipeline(self.store, self.converter)
        self.assertEqual([r.as_csv_row() for r in rows1], [r.as_csv_row() for r in rows2])

    def test_write_and_read_back_output_csv(self):
        import tempfile
        rows, _, _ = op.run_production_pipeline(self.store, self.converter)
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "output.csv"
            op.write_output_csv(rows, out_path)
            read_back = op.read_output_csv(out_path)
        self.assertEqual(len(read_back), 250)
        self.assertEqual(list(read_back[0].keys()), list(op.OUTPUT_COLUMNS))
        ids = [r["request_id"] for r in read_back]
        self.assertEqual(len(ids), len(set(ids)), "duplicate request_id found")
        request_ids = {r.request_id for r in self.store.requests}
        self.assertEqual(set(ids), request_ids)


if __name__ == "__main__":
    unittest.main()
