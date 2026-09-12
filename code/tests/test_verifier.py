"""Tests for code/verifier.py.

Builds PaymentPlan candidates directly (rather than via payment_plans.py's
generator) wherever a test needs to exercise a specific, sometimes
deliberately-broken, structural shape -- "do not trust generated plans"
cuts both ways: the verifier must catch a bad plan regardless of how it
was constructed.
"""

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_loader import FinancialEvent, PaymentOption, Request  # noqa: E402
from financial_state import EventTreatment, FinancialState, NormalizedEvent  # noqa: E402
from payment_plans import PaymentPlan, reconstruct_installment_dates  # noqa: E402
from verifier import verify_plan  # noqa: E402

D = date.fromisoformat


def make_request(
    request_id="request_1", user_id="user_1", request_date="2024-01-01",
    requested_amount="1000", desired_completion_date="2024-02-01", allows_partial_payment=True,
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


def full_payment_plan(amount="1000", pay_date="2024-01-01"):
    return PaymentPlan(
        method="full_payment", payment_dates=(D(pay_date),), payment_amounts=(Decimal(amount),),
        number_of_payments=1, total_payable=Decimal(amount), financing_fee=Decimal(0),
        full_payment_date=D(pay_date),
    )


class FullPaymentVerificationTests(unittest.TestCase):
    def test_safe_full_payment_is_valid(self):
        request = make_request(requested_amount="1000")
        state = make_state(balance="10000", minimum="1000")
        result = verify_plan(full_payment_plan("1000"), request, state, {})
        self.assertTrue(result.valid, result.errors)

    def test_unsafe_full_payment_is_rejected(self):
        request = make_request(requested_amount="9500")
        state = make_state(balance="10000", minimum="1000")  # only 9000 safe
        result = verify_plan(full_payment_plan("9500"), request, state, {})
        self.assertFalse(result.valid)
        self.assertTrue(any("not safe" in e for e in result.errors))

    def test_full_payment_exactly_at_boundary_succeeds(self):
        request = make_request(requested_amount="9000")
        state = make_state(balance="10000", minimum="1000")
        result = verify_plan(full_payment_plan("9000"), request, state, {})
        self.assertTrue(result.valid, result.errors)

    def test_full_payment_one_unit_above_boundary_fails(self):
        request = make_request(requested_amount="9000.01")
        state = make_state(balance="10000", minimum="1000")
        result = verify_plan(full_payment_plan("9000.01"), request, state, {})
        self.assertFalse(result.valid)

    def test_user_rejecting_full_payment_is_rejected(self):
        request = make_request(requested_amount="1000")
        state = make_state(balance="10000", minimum="1000", payment_methods_user_will_consider=["installments"])
        result = verify_plan(full_payment_plan("1000"), request, state, {})
        self.assertFalse(result.valid)
        self.assertTrue(any("does not accept" in e for e in result.errors))

    def test_full_payment_wrong_amount_is_rejected(self):
        request = make_request(requested_amount="1000")
        state = make_state()
        result = verify_plan(full_payment_plan("999"), request, state, {})
        self.assertFalse(result.valid)

    def test_full_payment_wrong_number_of_payments_is_rejected(self):
        request = make_request(requested_amount="1000")
        state = make_state()
        bad = PaymentPlan(
            method="full_payment", payment_dates=(request.request_date, request.request_date),
            payment_amounts=(Decimal(500), Decimal(500)), number_of_payments=2,
            total_payable=Decimal(1000), financing_fee=Decimal(0), full_payment_date=request.request_date,
        )
        result = verify_plan(bad, request, state, {})
        self.assertFalse(result.valid)


class StructuralRuleTests(unittest.TestCase):
    def test_rejects_payment_before_request_date(self):
        request = make_request(request_date="2024-01-10", requested_amount="1000")
        state = make_state()
        bad = full_payment_plan("1000", pay_date="2024-01-05")
        result = verify_plan(bad, request, state, {})
        self.assertFalse(result.valid)
        self.assertTrue(any("before request_date" in e for e in result.errors))

    def test_rejects_payment_after_deadline(self):
        request = make_request(desired_completion_date="2024-01-10", requested_amount="1000")
        state = make_state()
        bad = PaymentPlan(
            method="wait", payment_dates=(D("2024-01-15"),), payment_amounts=(Decimal("1000"),),
            number_of_payments=1, total_payable=Decimal("1000"), financing_fee=Decimal(0),
            full_payment_date=D("2024-01-15"),
        )
        result = verify_plan(bad, request, state, {})
        self.assertFalse(result.valid)
        self.assertTrue(any("after desired_completion_date" in e or "after the deadline" in e for e in result.errors))

    def test_rejects_incorrect_payment_sum(self):
        request = make_request(requested_amount="1000", allows_partial_payment=True)
        state = make_state()
        bad = PaymentPlan(
            method="partial_payment", payment_dates=(request.request_date, D("2024-01-15")),
            payment_amounts=(Decimal(400), Decimal(400)),  # doesn't sum to 1000
            number_of_payments=2, total_payable=Decimal(800), financing_fee=Decimal(0),
            full_payment_date=D("2024-01-15"),
        )
        result = verify_plan(bad, request, state, {})
        self.assertFalse(result.valid)

    def test_rejects_non_positive_amount(self):
        request = make_request(requested_amount="1000")
        state = make_state()
        bad = PaymentPlan(
            method="full_payment", payment_dates=(request.request_date,), payment_amounts=(Decimal(0),),
            number_of_payments=1, total_payable=Decimal(0), financing_fee=Decimal(0),
            full_payment_date=request.request_date,
        )
        result = verify_plan(bad, request, state, {})
        self.assertFalse(result.valid)

    def test_rejects_unsupported_method(self):
        request = make_request(requested_amount="1000")
        state = make_state()
        bad = PaymentPlan(
            method="cashback", payment_dates=(request.request_date,), payment_amounts=(Decimal(1000),),
            number_of_payments=1, total_payable=Decimal(1000), financing_fee=Decimal(0),
            full_payment_date=request.request_date,
        )
        result = verify_plan(bad, request, state, {})
        self.assertFalse(result.valid)
        self.assertTrue(any("unsupported" in e for e in result.errors))

    def test_rejects_spending_changes_present(self):
        request = make_request(requested_amount="1000")
        state = make_state()
        bad = PaymentPlan(
            method="full_payment", payment_dates=(request.request_date,), payment_amounts=(Decimal(1000),),
            number_of_payments=1, total_payable=Decimal(1000), financing_fee=Decimal(0),
            full_payment_date=request.request_date, spending_changes=("stop:event_1",),
        )
        result = verify_plan(bad, request, state, {})
        self.assertFalse(result.valid)

    def test_rejects_unordered_dates(self):
        request = make_request(requested_amount="1000", allows_partial_payment=True, desired_completion_date="2024-03-01")
        state = make_state()
        bad = PaymentPlan(
            method="partial_payment", payment_dates=(D("2024-01-15"), request.request_date),
            payment_amounts=(Decimal(500), Decimal(500)), number_of_payments=2,
            total_payable=Decimal(1000), financing_fee=Decimal(0), full_payment_date=D("2024-01-15"),
        )
        result = verify_plan(bad, request, state, {})
        self.assertFalse(result.valid)
        self.assertTrue(any("chronological" in e for e in result.errors))


class PartialPaymentVerificationTests(unittest.TestCase):
    def test_rejects_when_request_disallows_partial_payment(self):
        request = make_request(requested_amount="1000", allows_partial_payment=False)
        state = make_state()
        plan = PaymentPlan(
            method="partial_payment", payment_dates=(request.request_date, D("2024-01-15")),
            payment_amounts=(Decimal(600), Decimal(400)), number_of_payments=2,
            total_payable=Decimal(1000), financing_fee=Decimal(0), full_payment_date=D("2024-01-15"),
        )
        result = verify_plan(plan, request, state, {})
        self.assertFalse(result.valid)

    def test_rejects_wrong_number_of_payments(self):
        request = make_request(requested_amount="1000", allows_partial_payment=True)
        state = make_state()
        plan = PaymentPlan(
            method="partial_payment", payment_dates=(request.request_date,), payment_amounts=(Decimal(1000),),
            number_of_payments=1, total_payable=Decimal(1000), financing_fee=Decimal(0),
            full_payment_date=request.request_date,
        )
        result = verify_plan(plan, request, state, {})
        self.assertFalse(result.valid)

    def test_rejects_first_payment_not_matching_recomputed_safe_amount(self):
        request = make_request(requested_amount="10000", allows_partial_payment=True, desired_completion_date="2024-06-01")
        state = make_state(balance="5000", minimum="1000", future_income_events=[make_future_income_event()])
        # Real safe amount today is 4000; claim something else.
        plan = PaymentPlan(
            method="partial_payment", payment_dates=(request.request_date, D("2024-01-20")),
            payment_amounts=(Decimal(3000), Decimal(7000)), number_of_payments=2,
            total_payable=Decimal(10000), financing_fee=Decimal(0), full_payment_date=D("2024-01-20"),
        )
        result = verify_plan(plan, request, state, {})
        self.assertFalse(result.valid)
        self.assertTrue(any("independently" in e for e in result.errors))

    def test_rejects_second_payment_not_equal_to_remainder(self):
        request = make_request(requested_amount="10000", allows_partial_payment=True, desired_completion_date="2024-06-01")
        state = make_state(balance="5000", minimum="1000", future_income_events=[make_future_income_event()])
        safe = Decimal("4000")
        plan = PaymentPlan(
            method="partial_payment", payment_dates=(request.request_date, D("2024-01-20")),
            payment_amounts=(safe, Decimal("5999")),  # should be 6000
            number_of_payments=2, total_payable=safe + Decimal("5999"), financing_fee=Decimal(0),
            full_payment_date=D("2024-01-20"),
        )
        result = verify_plan(plan, request, state, {})
        self.assertFalse(result.valid)


class InstallmentVerificationTests(unittest.TestCase):
    def test_valid_installment_matching_the_option_exactly(self):
        request = make_request(requested_amount="1050", desired_completion_date="2024-04-01")
        option = make_option()
        state = make_state(balance="100000", minimum="1000", max_installment_months=12)
        plan = PaymentPlan(
            method="installments", payment_dates=reconstruct_installment_dates(option),
            payment_amounts=(option.payment_amount,) * option.number_of_payments,
            number_of_payments=option.number_of_payments, total_payable=option.total_payable_amount,
            financing_fee=option.financing_fee, full_payment_date=reconstruct_installment_dates(option)[-1],
            source_payment_option_id=option.payment_option_id,
        )
        result = verify_plan(plan, request, state, {option.payment_option_id: option})
        self.assertTrue(result.valid, result.errors)

    def test_rejects_invented_schedule_not_matching_any_option(self):
        request = make_request(requested_amount="1050")
        option = make_option()
        state = make_state(balance="100000", minimum="1000", max_installment_months=12)
        # Invented: different dates from the supplied option.
        plan = PaymentPlan(
            method="installments", payment_dates=(D("2024-01-01"), D("2024-02-01"), D("2024-03-01")),
            payment_amounts=(option.payment_amount,) * 3, number_of_payments=3,
            total_payable=option.total_payable_amount, financing_fee=option.financing_fee,
            full_payment_date=D("2024-03-01"), source_payment_option_id=option.payment_option_id,
        )
        result = verify_plan(plan, request, state, {option.payment_option_id: option})
        self.assertFalse(result.valid)
        self.assertTrue(any("do not match the option's schedule" in e for e in result.errors))

    def test_rejects_option_exceeding_user_max_months(self):
        request = make_request(requested_amount="1050")
        option = make_option(number_of_payments=6, payment_frequency_days=30)
        state = make_state(balance="100000", minimum="1000", max_installment_months=3)
        plan = PaymentPlan(
            method="installments", payment_dates=reconstruct_installment_dates(option),
            payment_amounts=(option.payment_amount,) * option.number_of_payments,
            number_of_payments=option.number_of_payments, total_payable=option.total_payable_amount,
            financing_fee=option.financing_fee, full_payment_date=reconstruct_installment_dates(option)[-1],
            source_payment_option_id=option.payment_option_id,
        )
        result = verify_plan(plan, request, state, {option.payment_option_id: option})
        self.assertFalse(result.valid)

    def test_rejects_amount_mismatch_against_option(self):
        request = make_request(requested_amount="1050")
        option = make_option()
        state = make_state(balance="100000", minimum="1000", max_installment_months=12)
        plan = PaymentPlan(
            method="installments", payment_dates=reconstruct_installment_dates(option),
            payment_amounts=(Decimal("351"),) * option.number_of_payments,  # option says 350
            number_of_payments=option.number_of_payments, total_payable=option.total_payable_amount,
            financing_fee=option.financing_fee, full_payment_date=reconstruct_installment_dates(option)[-1],
            source_payment_option_id=option.payment_option_id,
        )
        result = verify_plan(plan, request, state, {option.payment_option_id: option})
        self.assertFalse(result.valid)

    def test_rejects_financing_fee_mismatch(self):
        request = make_request(requested_amount="1050")
        option = make_option()
        state = make_state(balance="100000", minimum="1000", max_installment_months=12)
        plan = PaymentPlan(
            method="installments", payment_dates=reconstruct_installment_dates(option),
            payment_amounts=(option.payment_amount,) * option.number_of_payments,
            number_of_payments=option.number_of_payments, total_payable=option.total_payable_amount,
            financing_fee=Decimal("999"),  # option says 50
            full_payment_date=reconstruct_installment_dates(option)[-1],
            source_payment_option_id=option.payment_option_id,
        )
        result = verify_plan(plan, request, state, {option.payment_option_id: option})
        self.assertFalse(result.valid)

    def test_rejects_total_payable_mismatch(self):
        request = make_request(requested_amount="1050")
        option = make_option()
        state = make_state(balance="100000", minimum="1000", max_installment_months=12)
        plan = PaymentPlan(
            method="installments", payment_dates=reconstruct_installment_dates(option),
            payment_amounts=(option.payment_amount,) * option.number_of_payments,
            number_of_payments=option.number_of_payments,
            total_payable=Decimal("999999"),  # doesn't match option, and doesn't match sum either
            financing_fee=option.financing_fee, full_payment_date=reconstruct_installment_dates(option)[-1],
            source_payment_option_id=option.payment_option_id,
        )
        result = verify_plan(plan, request, state, {option.payment_option_id: option})
        self.assertFalse(result.valid)

    def test_rejects_unknown_payment_option_id(self):
        request = make_request(requested_amount="1050")
        option = make_option()
        state = make_state(balance="100000", minimum="1000", max_installment_months=12)
        plan = PaymentPlan(
            method="installments", payment_dates=reconstruct_installment_dates(option),
            payment_amounts=(option.payment_amount,) * option.number_of_payments,
            number_of_payments=option.number_of_payments, total_payable=option.total_payable_amount,
            financing_fee=option.financing_fee, full_payment_date=reconstruct_installment_dates(option)[-1],
            source_payment_option_id="payment_option_does_not_exist",
        )
        result = verify_plan(plan, request, state, {option.payment_option_id: option})
        self.assertFalse(result.valid)

    def test_multi_payment_installment_forecast_is_verified_as_one_scenario(self):
        # Each individual installment fits under the floor alone, but two
        # of them together (both drawn from a tight balance) do not.
        option = make_option(payment_amount="3000", number_of_payments=2, payment_frequency_days=30,
                              financing_fee="0", total_payable_amount="6000")
        request = make_request(requested_amount="6000", desired_completion_date="2024-06-01")
        state = make_state(balance="6500", minimum="1000")  # only 5500 headroom total
        plan = PaymentPlan(
            method="installments", payment_dates=reconstruct_installment_dates(option),
            payment_amounts=(Decimal("3000"), Decimal("3000")), number_of_payments=2,
            total_payable=Decimal("6000"), financing_fee=Decimal(0),
            full_payment_date=reconstruct_installment_dates(option)[-1],
            source_payment_option_id=option.payment_option_id,
        )
        result = verify_plan(plan, request, state, {option.payment_option_id: option})
        self.assertFalse(result.valid)
        self.assertTrue(any("not safe" in e for e in result.errors))


class WaitVerificationTests(unittest.TestCase):
    def test_valid_wait_plan(self):
        request = make_request(requested_amount="8000", desired_completion_date="2024-02-01")
        state = make_state(balance="100", minimum="50", future_income_events=[make_future_income_event()])
        plan = PaymentPlan(
            method="wait", payment_dates=(D("2024-01-20"),), payment_amounts=(Decimal("8000"),),
            number_of_payments=1, total_payable=Decimal("8000"), financing_fee=Decimal(0),
            full_payment_date=D("2024-01-20"),
        )
        result = verify_plan(plan, request, state, {})
        self.assertTrue(result.valid, result.errors)

    def test_rejects_wait_on_request_date(self):
        request = make_request(requested_amount="500")
        state = make_state(balance="10000", minimum="1000")
        plan = PaymentPlan(
            method="wait", payment_dates=(request.request_date,), payment_amounts=(Decimal("500"),),
            number_of_payments=1, total_payable=Decimal("500"), financing_fee=Decimal(0),
            full_payment_date=request.request_date,
        )
        result = verify_plan(plan, request, state, {})
        self.assertFalse(result.valid)

    def test_rejects_wait_with_two_payments(self):
        request = make_request(requested_amount="1000", desired_completion_date="2024-03-01")
        state = make_state()
        plan = PaymentPlan(
            method="wait", payment_dates=(D("2024-01-10"), D("2024-01-20")),
            payment_amounts=(Decimal(500), Decimal(500)), number_of_payments=2,
            total_payable=Decimal(1000), financing_fee=Decimal(0), full_payment_date=D("2024-01-20"),
        )
        result = verify_plan(plan, request, state, {})
        self.assertFalse(result.valid)

    def test_rejects_unsafe_wait_date(self):
        request = make_request(requested_amount="8000", desired_completion_date="2024-06-01")
        state = make_state(balance="100", minimum="50")  # no income -- never actually safe
        plan = PaymentPlan(
            method="wait", payment_dates=(D("2024-03-01"),), payment_amounts=(Decimal("8000"),),
            number_of_payments=1, total_payable=Decimal("8000"), financing_fee=Decimal(0),
            full_payment_date=D("2024-03-01"),
        )
        result = verify_plan(plan, request, state, {})
        self.assertFalse(result.valid)


if __name__ == "__main__":
    unittest.main()
