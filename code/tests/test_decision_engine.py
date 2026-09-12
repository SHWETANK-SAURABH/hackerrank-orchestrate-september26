"""Tests for code/decision_engine.py.

Exercises the status hierarchy, plan ranking, and the independence of
amount_safe_to_pay/earliest_date_for_full_payment from payment-method
preference, installment options, and the recommended plan itself.
"""

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_loader import DataStore, FinancialEvent, PaymentOption, Request, default_dataset_dir  # noqa: E402
from currency import CurrencyConverter  # noqa: E402
from financial_state import EventTreatment, FinancialState, NormalizedEvent, build_financial_state  # noqa: E402
from decision_engine import DecisionResult, make_decision  # noqa: E402

D = date.fromisoformat


def make_request(
    request_id="request_1", user_id="user_1", request_date="2024-01-01",
    requested_amount="1000", desired_completion_date="2024-06-01", allows_partial_payment=True,
) -> Request:
    return Request(
        request_id=request_id, user_id=user_id, request_date=D(request_date), request_type="purchase",
        requested_amount=Decimal(requested_amount), desired_completion_date=D(desired_completion_date),
        allows_partial_payment=allows_partial_payment, request_text="test",
    )


def make_option(
    payment_option_id="payment_option_1", request_id="request_1", payment_method="installments",
    payment_amount="350", number_of_payments=3, first_payment_date="2024-01-05",
    payment_frequency_days=30, financing_fee="50", total_payable_amount="1050",
) -> PaymentOption:
    return PaymentOption(
        payment_option_id=payment_option_id, request_id=request_id, payment_method=payment_method,
        payment_amount=Decimal(payment_amount), number_of_payments=number_of_payments,
        first_payment_date=D(first_payment_date), payment_frequency_days=payment_frequency_days,
        financing_fee=Decimal(financing_fee), total_payable_amount=Decimal(total_payable_amount),
    )


def make_state(balance="10000", minimum="1000", **overrides) -> FinancialState:
    fields = dict(
        user_id="user_1", home_currency="USD",
        current_available_balance=Decimal(balance), minimum_balance_to_keep=Decimal(minimum),
        payment_methods_user_will_consider=["full_payment", "partial_payment", "installments"],
        max_installment_months=12,
    )
    fields.update(overrides)
    return FinancialState(**fields)


def make_future_income_event(event_id="income_1", amount="8000", event_date="2024-01-20") -> NormalizedEvent:
    ev_date = D(event_date)
    raw = FinancialEvent(
        event_id=event_id, user_id="user_1", event_type="income", description="Salary",
        category="salary", direction="credit", amount=Decimal(amount), currency="USD",
        event_date=ev_date, settlement_date=ev_date, status="scheduled", linked_event_id=None,
        flexibility="fixed", minimum_allowed_amount=None,
    )
    return NormalizedEvent(
        event_id=event_id, user_id="user_1", event_type="income", description="Salary",
        category="salary", direction="credit", original_amount=Decimal(amount), original_currency="USD",
        amount_home_currency=Decimal(amount), event_date=ev_date, settlement_date=ev_date, status="scheduled",
        linked_event_id=None, flexibility="fixed", minimum_allowed_amount=None,
        treatment=EventTreatment.SCHEDULED_INCOME, linked_info=None, raw_event=raw,
    )


class StatusHierarchyTests(unittest.TestCase):
    def test_safe_today_is_affordable_now(self):
        request = make_request(requested_amount="1000")
        state = make_state(balance="10000", minimum="1000")
        decision = make_decision(request, state, [])
        self.assertEqual(decision.affordability_status, "affordable_now")
        self.assertEqual(decision.recommended_payment_method, "full_payment")
        self.assertIsNotNone(decision.payment_plan)

    def test_valid_installment_plan_is_affordable_with_plan(self):
        # Partial payment is disallowed so only the installment option can
        # compete; full payment today is unsafe, but income arriving
        # between installments makes the whole schedule safe.
        request = make_request(requested_amount="1050", desired_completion_date="2024-04-01", allows_partial_payment=False)
        state = make_state(balance="400", minimum="50", max_installment_months=12, future_income_events=[
            make_future_income_event("income_1", amount="400", event_date="2024-01-20"),
            make_future_income_event("income_2", amount="400", event_date="2024-02-20"),
        ])
        option = make_option()
        decision = make_decision(request, state, [option])
        self.assertEqual(decision.affordability_status, "affordable_with_plan")
        self.assertEqual(decision.recommended_payment_method, "installments")

    def test_safe_only_later_is_affordable_later(self):
        request = make_request(requested_amount="8000", desired_completion_date="2024-06-01", allows_partial_payment=False)
        state = make_state(balance="100", minimum="50", future_income_events=[make_future_income_event()])
        decision = make_decision(request, state, [])
        self.assertEqual(decision.affordability_status, "affordable_later")
        self.assertEqual(decision.recommended_payment_method, "wait")
        self.assertEqual(decision.payment_plan.payment_dates, (D("2024-01-20"),))

    def test_no_valid_plan_is_not_affordable(self):
        request = make_request(requested_amount="1000000", desired_completion_date="2024-02-01")
        state = make_state(balance="100", minimum="50")
        decision = make_decision(request, state, [])
        self.assertEqual(decision.affordability_status, "not_affordable")
        self.assertEqual(decision.recommended_payment_method, "not_recommended")
        self.assertIsNone(decision.payment_plan)

    def test_affordable_now_beats_an_also_valid_wait_or_installment_candidate(self):
        # Even when other tiers WOULD also be technically constructible,
        # a safe-today full payment must win -- the hierarchy is resolved
        # tier-first, never by one flat comparison across all candidates.
        request = make_request(requested_amount="500", desired_completion_date="2024-06-01")
        state = make_state(balance="10000", minimum="1000")
        decision = make_decision(request, state, [])
        self.assertEqual(decision.affordability_status, "affordable_now")


class RankingTests(unittest.TestCase):
    def test_lower_total_payable_wins_over_higher_fee_option(self):
        request = make_request(requested_amount="1000", desired_completion_date="2024-06-01")
        state = make_state(balance="0", minimum="0", max_installment_months=12,
                            future_income_events=[make_future_income_event(amount="20000")])
        cheap = make_option(payment_option_id="payment_option_1", payment_amount="200", number_of_payments=5,
                             financing_fee="0", total_payable_amount="1000", first_payment_date="2024-01-21")
        expensive = make_option(payment_option_id="payment_option_2", payment_amount="220", number_of_payments=5,
                                 financing_fee="100", total_payable_amount="1100", first_payment_date="2024-01-21")
        decision = make_decision(request, state, [cheap, expensive])
        self.assertEqual(decision.payment_plan.source_payment_option_id, "payment_option_1")

    def test_fewer_payments_wins_when_total_payable_ties(self):
        request = make_request(requested_amount="1000", desired_completion_date="2024-06-01")
        state = make_state(balance="0", minimum="0", max_installment_months=12,
                            future_income_events=[make_future_income_event(amount="20000")])
        two_payments = make_option(payment_option_id="payment_option_1", payment_amount="500",
                                    number_of_payments=2, financing_fee="0", total_payable_amount="1000",
                                    first_payment_date="2024-01-21")
        four_payments = make_option(payment_option_id="payment_option_2", payment_amount="250",
                                     number_of_payments=4, financing_fee="0", total_payable_amount="1000",
                                     first_payment_date="2024-01-21")
        decision = make_decision(request, state, [two_payments, four_payments])
        self.assertEqual(decision.payment_plan.source_payment_option_id, "payment_option_1")

    def test_earlier_start_wins_when_total_and_count_tie(self):
        request = make_request(requested_amount="1000", desired_completion_date="2024-08-01")
        state = make_state(balance="0", minimum="0", max_installment_months=12,
                            future_income_events=[make_future_income_event(amount="20000", event_date="2024-01-25")])
        earlier = make_option(payment_option_id="payment_option_1", payment_amount="500", number_of_payments=2,
                               financing_fee="0", total_payable_amount="1000", first_payment_date="2024-01-26")
        later = make_option(payment_option_id="payment_option_2", payment_amount="500", number_of_payments=2,
                             financing_fee="0", total_payable_amount="1000", first_payment_date="2024-02-26")
        decision = make_decision(request, state, [earlier, later])
        self.assertEqual(decision.payment_plan.source_payment_option_id, "payment_option_1")

    def test_lower_payment_option_id_is_the_final_tiebreak(self):
        request = make_request(requested_amount="1000", desired_completion_date="2024-06-01")
        state = make_state(balance="0", minimum="0", max_installment_months=12,
                            future_income_events=[make_future_income_event(amount="20000")])
        # Identical in every other respect -- only the option id differs.
        option_a = make_option(payment_option_id="payment_option_2", payment_amount="500", number_of_payments=2,
                                financing_fee="0", total_payable_amount="1000", first_payment_date="2024-01-21")
        option_b = make_option(payment_option_id="payment_option_10", payment_amount="500", number_of_payments=2,
                                financing_fee="0", total_payable_amount="1000", first_payment_date="2024-01-21")
        decision = make_decision(request, state, [option_a, option_b])
        self.assertEqual(decision.payment_plan.source_payment_option_id, "payment_option_2")

    def test_ranking_is_deterministic_across_repeated_calls(self):
        request = make_request(requested_amount="1000", desired_completion_date="2024-06-01")
        state = make_state(balance="0", minimum="0", max_installment_months=12,
                            future_income_events=[make_future_income_event(amount="20000")])
        option_a = make_option(payment_option_id="payment_option_2", payment_amount="500", number_of_payments=2,
                                financing_fee="0", total_payable_amount="1000", first_payment_date="2024-01-21")
        option_b = make_option(payment_option_id="payment_option_1", payment_amount="500", number_of_payments=2,
                                financing_fee="0", total_payable_amount="1000", first_payment_date="2024-01-21")
        first = make_decision(request, state, [option_a, option_b])
        second = make_decision(request, state, [option_a, option_b])
        self.assertEqual(first.payment_plan.source_payment_option_id, second.payment_plan.source_payment_option_id)


class EarliestDateIndependenceTests(unittest.TestCase):
    def test_earliest_date_does_not_depend_on_payment_preferences(self):
        request = make_request(requested_amount="8000", desired_completion_date="2024-06-01")
        base = dict(balance="100", minimum="50", future_income_events=[make_future_income_event()])
        state_a = make_state(payment_methods_user_will_consider=["full_payment"], max_installment_months=None, **base)
        state_b = make_state(payment_methods_user_will_consider=["installments"], max_installment_months=6, **base)
        decision_a = make_decision(request, state_a, [])
        decision_b = make_decision(request, state_b, [])
        self.assertEqual(decision_a.earliest_date_for_full_payment, decision_b.earliest_date_for_full_payment)

    def test_earliest_date_does_not_depend_on_installment_options(self):
        request = make_request(requested_amount="8000", desired_completion_date="2024-06-01")
        state = make_state(balance="100", minimum="50", future_income_events=[make_future_income_event()],
                            max_installment_months=12)
        without_options = make_decision(request, state, [])
        with_options = make_decision(request, state, [make_option(payment_amount="3000", number_of_payments=3,
                                                                    total_payable_amount="9000", financing_fee="1000")])
        self.assertEqual(without_options.earliest_date_for_full_payment, with_options.earliest_date_for_full_payment)

    def test_earliest_date_is_not_replaced_by_the_recommended_plans_completion_date(self):
        # The recommended plan here is installments completing later than
        # the raw baseline earliest-safe-full-payment date.
        request = make_request(requested_amount="1050", desired_completion_date="2024-04-01", allows_partial_payment=False)
        state = make_state(balance="200", minimum="50", max_installment_months=12,
                            future_income_events=[make_future_income_event(amount="2000", event_date="2024-01-10")])
        # First installment falls after the income arrives, so the whole
        # schedule is safe even though the raw full-amount baseline (using
        # the same income) becomes safe on 2024-01-10 -- well before the
        # installment plan finishes on 2024-03-15.
        option = make_option(first_payment_date="2024-01-15")
        decision = make_decision(request, state, [option])
        self.assertEqual(decision.recommended_payment_method, "installments")
        self.assertNotEqual(decision.earliest_date_for_full_payment, decision.payment_plan.full_payment_date)
        self.assertEqual(decision.earliest_date_for_full_payment, D("2024-01-10"))

    def test_amount_safe_to_pay_is_independent_of_recommended_method(self):
        request = make_request(requested_amount="10000", desired_completion_date="2024-06-01")
        state = make_state(balance="5000", minimum="1000")  # no future income -> nothing but not_affordable
        decision = make_decision(request, state, [])
        self.assertEqual(decision.affordability_status, "not_affordable")
        self.assertEqual(decision.amount_safe_to_pay, Decimal("4000"))  # still reported, even though nothing is recommended


class EdgeCaseTests(unittest.TestCase):
    def test_zero_requested_amount(self):
        request = make_request(requested_amount="0")
        state = make_state()
        decision = make_decision(request, state, [])
        self.assertEqual(decision.affordability_status, "affordable_now")
        self.assertEqual(decision.amount_safe_to_pay, Decimal("0"))

    def test_negative_requested_amount_raises(self):
        request = make_request(requested_amount="-1")
        state = make_state()
        with self.assertRaises(ValueError):
            make_decision(request, state, [])

    def test_user_accepting_no_immediate_methods_falls_back_to_wait_or_not_recommended(self):
        request = make_request(requested_amount="8000", desired_completion_date="2024-06-01")
        state = make_state(balance="100", minimum="50", payment_methods_user_will_consider=[],
                            future_income_events=[make_future_income_event()])
        decision = make_decision(request, state, [])
        self.assertEqual(decision.affordability_status, "not_affordable")
        self.assertEqual(decision.recommended_payment_method, "not_recommended")

    def test_amount_safe_to_pay_never_exceeds_requested_amount(self):
        request = make_request(requested_amount="500")
        state = make_state(balance="1000000", minimum="0")
        decision = make_decision(request, state, [])
        self.assertLessEqual(decision.amount_safe_to_pay, request.requested_amount)

    def test_amount_safe_to_pay_never_negative(self):
        request = make_request(requested_amount="500")
        state = make_state(balance="0", minimum="1000")
        decision = make_decision(request, state, [])
        self.assertGreaterEqual(decision.amount_safe_to_pay, Decimal("0"))

    def test_max_installment_months_none_means_no_installments_offered(self):
        request = make_request(requested_amount="1050", desired_completion_date="2024-04-01")
        state = make_state(balance="0", minimum="0", max_installment_months=None,
                            future_income_events=[make_future_income_event(amount="2000")])
        decision = make_decision(request, state, [make_option()])
        self.assertNotEqual(decision.recommended_payment_method, "installments")


class RealDatasetDecisionEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not default_dataset_dir().exists():
            raise unittest.SkipTest("dataset/ not found; skipping integration tests")
        cls.store = DataStore.load(strict=True)
        cls.converter = CurrencyConverter.from_data_store(cls.store)

    def test_every_verified_candidate_that_is_valid_is_actually_safe(self):
        # Cross-check: for a sample of real requests, every candidate the
        # verifier calls valid really does keep the balance at/above the
        # minimum for its own simulated scenario -- no invalid plan is
        # ever marked valid.
        import forecast
        for sample in self.store.sample_requests:
            state = build_financial_state(self.store, self.converter, sample.user_id)
            options = self.store.get_payment_options(sample.request_id)
            decision = make_decision(sample, state, options)
            for vc in decision.candidates:
                if not vc.verification.valid:
                    continue
                result = forecast.simulate_payments(
                    state, sample.request_date, list(zip(vc.plan.payment_dates, vc.plan.payment_amounts)))
                self.assertTrue(result.is_safe, f"{sample.request_id}: {vc.plan.method} marked valid but unsafe")

    def test_decision_is_produced_for_every_request_without_crashing(self):
        count = 0
        for request in list(self.store.requests) + list(self.store.sample_requests):
            state = build_financial_state(self.store, self.converter, request.user_id)
            options = self.store.get_payment_options(request.request_id)
            decision = make_decision(request, state, options)
            self.assertIn(decision.affordability_status, (
                "affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"))
            self.assertIn(decision.recommended_payment_method, (
                "full_payment", "partial_payment", "installments", "wait", "not_recommended"))
            self.assertGreaterEqual(decision.amount_safe_to_pay, Decimal("0"))
            self.assertLessEqual(decision.amount_safe_to_pay, request.requested_amount)
            count += 1
        self.assertEqual(count, len(self.store.requests) + len(self.store.sample_requests))


if __name__ == "__main__":
    unittest.main()
