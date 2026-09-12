"""Tests for code/payment_plans.py.

Fixtures build Request/PaymentOption/FinancialState directly as
dataclasses -- payment_plans.py has no DataStore/CurrencyConverter
dependency, so no fake store is needed (same style as test_forecast.py).
"""

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_loader import FinancialEvent, PaymentOption, Request  # noqa: E402
from financial_state import EventTreatment, FinancialState, NormalizedEvent  # noqa: E402
from payment_plans import (  # noqa: E402
    generate_candidate_plans,
    generate_full_payment_plan,
    generate_installment_plans,
    generate_partial_payment_plan,
    generate_wait_plan,
    installment_option_months,
    reconstruct_installment_dates,
)

D = date.fromisoformat


def make_request(
    request_id="request_1", user_id="user_1", request_date="2024-01-01",
    request_type="purchase", requested_amount="1000", desired_completion_date="2024-02-01",
    allows_partial_payment=True, request_text="test",
) -> Request:
    return Request(
        request_id=request_id, user_id=user_id, request_date=D(request_date), request_type=request_type,
        requested_amount=Decimal(requested_amount), desired_completion_date=D(desired_completion_date),
        allows_partial_payment=allows_partial_payment, request_text=request_text,
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


def make_state(balance="10000", minimum="1000", **overrides) -> FinancialState:
    fields = dict(
        user_id="user_1", home_currency="USD",
        current_available_balance=Decimal(balance), minimum_balance_to_keep=Decimal(minimum),
        payment_methods_user_will_consider=["full_payment", "partial_payment", "installments"],
        max_installment_months=12,
    )
    fields.update(overrides)
    return FinancialState(**fields)


class FullPaymentTests(unittest.TestCase):
    def test_generates_full_payment_on_request_date_for_requested_amount(self):
        request = make_request(requested_amount="1000")
        state = make_state()
        plan = generate_full_payment_plan(request, state)
        self.assertEqual(plan.method, "full_payment")
        self.assertEqual(plan.payment_dates, (request.request_date,))
        self.assertEqual(plan.payment_amounts, (Decimal("1000"),))
        self.assertEqual(plan.number_of_payments, 1)
        self.assertEqual(plan.total_payable, Decimal("1000"))
        self.assertEqual(plan.full_payment_date, request.request_date)

    def test_not_generated_when_user_rejects_full_payment(self):
        request = make_request()
        state = make_state(payment_methods_user_will_consider=["installments"])
        self.assertIsNone(generate_full_payment_plan(request, state))


class PartialPaymentTests(unittest.TestCase):
    def test_not_generated_when_request_disallows_partial_payment(self):
        request = make_request(allows_partial_payment=False)
        state = make_state()
        self.assertIsNone(generate_partial_payment_plan(request, state))

    def test_not_generated_when_user_rejects_partial_payment(self):
        request = make_request(allows_partial_payment=True)
        state = make_state(payment_methods_user_will_consider=["full_payment"])
        self.assertIsNone(generate_partial_payment_plan(request, state))

    def test_generated_when_eligible_and_safe_amount_is_between_zero_and_requested(self):
        # balance 5000, min 1000 -> safe today = 4000; an 8000 salary on
        # 01-20 lets the 6000 remainder clear comfortably by the deadline.
        request = make_request(requested_amount="10000", desired_completion_date="2024-06-01")
        state = make_state(balance="5000", minimum="1000", future_income_events=[make_future_income_event()])
        plan = generate_partial_payment_plan(request, state)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.method, "partial_payment")
        self.assertEqual(plan.number_of_payments, 2)
        self.assertTrue(Decimal("0") < plan.payment_amounts[0] < Decimal("10000"))

    def test_two_payments_sum_exactly_to_requested_amount(self):
        request = make_request(requested_amount="9999.99", desired_completion_date="2024-06-01")
        state = make_state(balance="5000", minimum="1000", future_income_events=[make_future_income_event()])
        plan = generate_partial_payment_plan(request, state)
        self.assertEqual(sum(plan.payment_amounts), request.requested_amount)
        self.assertEqual(plan.total_payable, request.requested_amount)

    def test_first_payment_is_on_request_date(self):
        request = make_request(requested_amount="10000", desired_completion_date="2024-06-01")
        state = make_state(balance="5000", minimum="1000", future_income_events=[make_future_income_event()])
        plan = generate_partial_payment_plan(request, state)
        self.assertEqual(plan.payment_dates[0], request.request_date)

    def test_second_payment_checked_with_first_already_applied(self):
        # If the second leg were sized against a forecast that "forgot" the
        # first payment, it would look safe (there's no OTHER obligation
        # to breach the floor). With the first payment correctly still
        # applied, the maximum extractable today is already exactly the
        # floor, so the remainder can never be safely paid without new
        # income -- and with none available, no plan is generated.
        request = make_request(requested_amount="10000", desired_completion_date="2024-06-01")
        state = make_state(balance="5000", minimum="1000")
        plan = generate_partial_payment_plan(request, state)
        self.assertIsNone(plan)

    def test_no_valid_second_date_within_deadline_rejects_the_plan(self):
        request = make_request(requested_amount="10000", desired_completion_date="2024-01-05")
        state = make_state(balance="5000", minimum="1000")
        self.assertIsNone(generate_partial_payment_plan(request, state))

    def test_not_generated_when_safe_amount_is_zero(self):
        request = make_request(requested_amount="10000")
        state = make_state(balance="1000", minimum="1000")  # zero headroom
        self.assertIsNone(generate_partial_payment_plan(request, state))

    def test_not_generated_when_safe_amount_covers_the_full_request(self):
        request = make_request(requested_amount="500")
        state = make_state(balance="10000", minimum="1000")  # way more than enough
        self.assertIsNone(generate_partial_payment_plan(request, state))


class InstallmentTests(unittest.TestCase):
    def test_reconstructs_dates_from_first_date_and_frequency(self):
        option = make_option(first_payment_date="2024-01-05", payment_frequency_days=30, number_of_payments=3)
        dates = reconstruct_installment_dates(option)
        self.assertEqual(dates, (D("2024-01-05"), D("2024-02-04"), D("2024-03-05")))

    def test_single_payment_option_has_one_date(self):
        option = make_option(number_of_payments=1, payment_frequency_days=None, first_payment_date="2024-01-05")
        self.assertEqual(reconstruct_installment_dates(option), (D("2024-01-05"),))

    def test_installment_option_months_for_monthly_cadence(self):
        option = make_option(payment_frequency_days=30, number_of_payments=6)
        self.assertEqual(installment_option_months(option), 6)

    def test_installment_option_months_none_for_non_monthly_cadence(self):
        option = make_option(payment_frequency_days=7, number_of_payments=6)
        self.assertIsNone(installment_option_months(option))

    def test_valid_supplied_option_is_accepted(self):
        request = make_request()
        state = make_state(max_installment_months=12)
        option = make_option(number_of_payments=3, payment_frequency_days=30)
        plans = generate_installment_plans(request, state, [option])
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].source_payment_option_id, option.payment_option_id)
        self.assertEqual(plans[0].payment_dates, reconstruct_installment_dates(option))
        self.assertEqual(plans[0].payment_amounts, (option.payment_amount,) * 3)
        self.assertEqual(plans[0].total_payable, option.total_payable_amount)
        self.assertEqual(plans[0].financing_fee, option.financing_fee)

    def test_user_max_months_rejects_oversized_option(self):
        request = make_request()
        state = make_state(max_installment_months=3)
        option = make_option(number_of_payments=6, payment_frequency_days=30)
        self.assertEqual(generate_installment_plans(request, state, [option]), [])

    def test_max_installment_months_none_rejects_all_installments(self):
        request = make_request()
        state = make_state(max_installment_months=None)
        option = make_option(number_of_payments=3, payment_frequency_days=30)
        self.assertEqual(generate_installment_plans(request, state, [option]), [])

    def test_user_rejecting_installments_generates_none(self):
        request = make_request()
        state = make_state(payment_methods_user_will_consider=["full_payment"], max_installment_months=12)
        option = make_option(number_of_payments=3, payment_frequency_days=30)
        self.assertEqual(generate_installment_plans(request, state, [option]), [])

    def test_full_payment_method_options_are_never_treated_as_installments(self):
        request = make_request()
        state = make_state(max_installment_months=12)
        option = make_option(payment_method="full_payment", number_of_payments=1, payment_frequency_days=None)
        self.assertEqual(generate_installment_plans(request, state, [option]), [])

    def test_options_for_a_different_request_are_ignored(self):
        request = make_request(request_id="request_1")
        state = make_state(max_installment_months=12)
        option = make_option(request_id="request_2", number_of_payments=3, payment_frequency_days=30)
        self.assertEqual(generate_installment_plans(request, state, [option]), [])

    def test_multiple_eligible_options_each_produce_a_candidate(self):
        request = make_request()
        state = make_state(max_installment_months=24)
        option_a = make_option(payment_option_id="payment_option_1", number_of_payments=3, payment_frequency_days=30)
        option_b = make_option(payment_option_id="payment_option_2", number_of_payments=6, payment_frequency_days=30)
        plans = generate_installment_plans(request, state, [option_a, option_b])
        self.assertEqual({p.source_payment_option_id for p in plans}, {"payment_option_1", "payment_option_2"})


class WaitTests(unittest.TestCase):
    def test_not_generated_when_user_rejects_full_payment(self):
        request = make_request()
        state = make_state(payment_methods_user_will_consider=["installments"])
        self.assertIsNone(generate_wait_plan(request, state))

    def test_not_generated_when_never_safe_by_deadline(self):
        request = make_request(requested_amount="1000000", desired_completion_date="2024-01-10")
        state = make_state(balance="100", minimum="50")
        self.assertIsNone(generate_wait_plan(request, state))

    def test_not_generated_when_already_safe_today(self):
        # That's affordable_now/full_payment territory, not "waiting".
        request = make_request(requested_amount="500")
        state = make_state(balance="10000", minimum="1000")
        self.assertIsNone(generate_wait_plan(request, state))

    def test_generated_with_the_baseline_earliest_safe_date(self):
        request = make_request(requested_amount="10000", desired_completion_date="2024-06-01")
        state = make_state(balance="100", minimum="50")
        plan = generate_wait_plan(request, state)
        self.assertIsNone(plan)  # never becomes safe with zero income at all -- confirms no fabrication


class CandidateGenerationTests(unittest.TestCase):
    def test_generate_candidate_plans_rejects_negative_requested_amount(self):
        request = make_request(requested_amount="-5")
        state = make_state()
        with self.assertRaises(ValueError):
            generate_candidate_plans(request, state, [])

    def test_only_eligible_methods_are_generated(self):
        request = make_request(allows_partial_payment=False)
        state = make_state(payment_methods_user_will_consider=["full_payment"], max_installment_months=None)
        plans = generate_candidate_plans(request, state, [make_option()])
        methods = {p.method for p in plans}
        self.assertIn("full_payment", methods)
        self.assertNotIn("partial_payment", methods)
        self.assertNotIn("installments", methods)


if __name__ == "__main__":
    unittest.main()
