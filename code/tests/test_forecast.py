"""Tests for code/forecast.py.

Phase 3 operates purely on an already-built FinancialState -- no
DataStore/CurrencyConverter dependency -- so fixtures here construct
FinancialState/NormalizedEvent/RecurrencePattern directly, no fake stores
or CSV fixtures needed (contrast with Phase 1/2's fixture approaches).
"""

import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from currency import CurrencyConverter  # noqa: E402
from data_loader import DataStore, FinancialEvent, default_dataset_dir  # noqa: E402
from financial_state import (  # noqa: E402
    EventTreatment,
    FinancialState,
    NormalizedEvent,
    RecurrenceFrequency,
    RecurrencePattern,
    build_financial_state,
)
from forecast import (  # noqa: E402
    ForecastEntry,
    build_forecast,
    can_safely_pay,
    describe_payment_safety,
    earliest_safe_payment_date,
    maximum_safe_payment,
    simulate_payment,
    simulate_payments,
)

D = date.fromisoformat

_TREATMENT_BY_STATUS_DIRECTION = {
    ("settled", "credit"): EventTreatment.EFFECTIVE_INCOME,
    ("settled", "debit"): EventTreatment.EFFECTIVE_EXPENSE,
    ("pending", "credit"): EventTreatment.PENDING_INCOME,
    ("pending", "debit"): EventTreatment.PENDING_EXPENSE,
    ("scheduled", "credit"): EventTreatment.SCHEDULED_INCOME,
    ("scheduled", "debit"): EventTreatment.SCHEDULED_EXPENSE,
    ("cancelled", "debit"): EventTreatment.CANCELLED,
    ("failed", "debit"): EventTreatment.FAILED,
}


def make_normalized_event(
    event_id,
    user_id="user_1",
    event_type="expense",
    description="Test event",
    category="shopping",
    direction="debit",
    amount="100",
    currency="USD",
    event_date="2024-01-10",
    settlement_date=None,
    status="scheduled",
    flexibility="fixed",
    minimum_allowed_amount=None,
) -> NormalizedEvent:
    amt = Decimal(amount) if amount is not None else None
    ev_date = D(event_date)
    settle = D(settlement_date) if settlement_date else None
    raw = FinancialEvent(
        event_id=event_id, user_id=user_id, event_type=event_type, description=description,
        category=category, direction=direction, amount=amt, currency=currency,
        event_date=ev_date, settlement_date=settle, status=status, linked_event_id=None,
        flexibility=flexibility,
        minimum_allowed_amount=Decimal(minimum_allowed_amount) if minimum_allowed_amount is not None else None,
    )
    treatment = _TREATMENT_BY_STATUS_DIRECTION.get((status, direction))
    if treatment is None:
        treatment = EventTreatment.UNRESOLVED_AMOUNT if amt is None else EventTreatment.UNKNOWN
    return NormalizedEvent(
        event_id=event_id, user_id=user_id, event_type=event_type, description=description,
        category=category, direction=direction, original_amount=amt, original_currency=currency,
        amount_home_currency=amt, event_date=ev_date, settlement_date=settle, status=status,
        linked_event_id=None, flexibility=flexibility, minimum_allowed_amount=None,
        treatment=treatment, linked_info=None, raw_event=raw,
    )


def make_pattern(
    category="rent",
    direction="debit",
    amount="1000",
    frequency=RecurrenceFrequency.MONTHLY,
    interval_days=30,
    next_occurrence="2024-01-05",
    occurrence_count=5,
    description="Recurring",
    source_ids=("e1", "e2", "e3"),
) -> RecurrencePattern:
    return RecurrencePattern(
        user_id="user_1", event_type="expense" if direction == "debit" else "income",
        direction=direction, category=category, description=description, frequency=frequency,
        typical_interval_days=interval_days,
        typical_amount_home_currency=Decimal(amount) if amount is not None else None,
        home_currency="USD",
        next_expected_occurrence=D(next_occurrence) if next_occurrence else None,
        occurrence_count=occurrence_count, confidence="high", flexibility="fixed",
        minimum_allowed_amount=None, source_event_ids=tuple(source_ids),
    )


def make_state(balance="1000", minimum="100", **overrides) -> FinancialState:
    fields = dict(
        user_id="user_1", home_currency="USD",
        current_available_balance=Decimal(balance), minimum_balance_to_keep=Decimal(minimum),
    )
    fields.update(overrides)
    return FinancialState(**fields)


START = D("2024-01-01")


# ---------------------------------------------------------------------------
# 1-3: starting balance / minimum balance / no historical replay
# ---------------------------------------------------------------------------


class BaselineTests(unittest.TestCase):
    def test_starting_balance_comes_from_financial_state(self):
        state = make_state(balance="54321.99", minimum="1000")
        result = build_forecast(state, START)
        self.assertEqual(result.starting_balance, Decimal("54321.99"))
        self.assertEqual(result.minimum_balance_to_keep, Decimal("1000"))

    def test_minimum_balance_is_enforced(self):
        state = make_state(balance="1000", minimum="900", future_expense_events=[
            make_normalized_event("e1", direction="debit", amount="150", status="scheduled", event_date="2024-01-10"),
        ])
        result = build_forecast(state, START)
        self.assertFalse(result.is_safe)
        self.assertEqual(result.first_violation_date, D("2024-01-10"))

    def test_historical_effective_events_are_not_replayed(self):
        # A huge historical expense in effective_events must NOT reduce the
        # starting balance again -- it's already reflected in `balance`.
        state = make_state(balance="1000", minimum="100", effective_events=[
            make_normalized_event("e1", direction="debit", amount="999999", status="settled", event_date="2023-06-01"),
        ])
        result = build_forecast(state, START)
        self.assertEqual(result.starting_balance, Decimal("1000"))
        self.assertTrue(result.is_safe)
        self.assertEqual(result.entries, ())


# ---------------------------------------------------------------------------
# 4-5, 25: known future events
# ---------------------------------------------------------------------------


class KnownFutureEventTests(unittest.TestCase):
    def test_future_scheduled_income_increases_balance_on_its_date(self):
        state = make_state(balance="100", minimum="50", future_income_events=[
            make_normalized_event("inc1", direction="credit", amount="500", status="scheduled", event_date="2024-01-15"),
        ])
        result = build_forecast(state, START)
        by_date = dict(result.balance_timeline)
        self.assertEqual(by_date[D("2024-01-15")], Decimal("600"))

    def test_future_scheduled_expense_decreases_balance_on_its_date(self):
        state = make_state(balance="1000", minimum="50", future_expense_events=[
            make_normalized_event("exp1", direction="debit", amount="300", status="scheduled", event_date="2024-01-20"),
        ])
        result = build_forecast(state, START)
        by_date = dict(result.balance_timeline)
        self.assertEqual(by_date[D("2024-01-20")], Decimal("700"))

    def test_income_beyond_horizon_is_not_used(self):
        state = make_state(balance="100", minimum="50", future_income_events=[
            make_normalized_event("inc1", direction="credit", amount="99999", status="scheduled", event_date="2024-05-01"),
        ])
        result = build_forecast(state, START, horizon_days=90)
        self.assertEqual(result.entries, ())
        self.assertEqual(result.minimum_projected_balance, Decimal("100"))


# ---------------------------------------------------------------------------
# 6-8, plus the "double-counted salary/expense" synthetic scenarios
# ---------------------------------------------------------------------------


class RecurrenceProjectionTests(unittest.TestCase):
    def test_recurring_income_is_projected(self):
        state = make_state(balance="0", minimum="0", recurring_income_patterns=[
            make_pattern(category="salary", direction="credit", amount="2000", next_occurrence="2024-01-10"),
        ])
        result = build_forecast(state, START, horizon_days=90)
        income_entries = [e for e in result.entries if e.source == "recurring_income"]
        # 90-day horizon, monthly cadence starting 01-10 -> ~3 occurrences
        self.assertGreaterEqual(len(income_entries), 3)
        self.assertTrue(all(e.amount == Decimal("2000") for e in income_entries))
        self.assertEqual(income_entries[0].pattern_source_event_ids, ("e1", "e2", "e3"))

    def test_recurring_expense_is_projected(self):
        state = make_state(balance="0", minimum="0", recurring_expense_patterns=[
            make_pattern(category="rent", direction="debit", amount="500", next_occurrence="2024-01-05"),
        ])
        result = build_forecast(state, START, horizon_days=90)
        expense_entries = [e for e in result.entries if e.source == "recurring_expense"]
        self.assertGreaterEqual(len(expense_entries), 3)
        self.assertTrue(all(e.amount == Decimal("-500") for e in expense_entries))

    def test_known_future_income_suppresses_matching_recurring_occurrence(self):
        # A known scheduled salary on 01-10 must not ALSO get a synthetic
        # recurring occurrence generated for the same date/category.
        state = make_state(
            balance="0", minimum="0",
            future_income_events=[make_normalized_event("inc1", direction="credit", amount="2000", category="salary",
                                                          status="scheduled", event_date="2024-01-10")],
            recurring_income_patterns=[make_pattern(category="salary", direction="credit", amount="2000", next_occurrence="2024-01-10")],
        )
        result = build_forecast(state, START, horizon_days=45)
        matching_date_entries = [e for e in result.entries if e.date == D("2024-01-10")]
        self.assertEqual(len(matching_date_entries), 1, "the known event must win, not both")
        self.assertEqual(matching_date_entries[0].source, "known_future")

    def test_known_future_expense_suppresses_matching_recurring_occurrence(self):
        state = make_state(
            balance="0", minimum="-100000",
            future_expense_events=[make_normalized_event("exp1", direction="debit", amount="500", category="rent",
                                                           status="scheduled", event_date="2024-01-05")],
            recurring_expense_patterns=[make_pattern(category="rent", direction="debit", amount="500", next_occurrence="2024-01-05")],
        )
        result = build_forecast(state, START, horizon_days=45)
        matching_date_entries = [e for e in result.entries if e.date == D("2024-01-05")]
        self.assertEqual(len(matching_date_entries), 1)
        self.assertEqual(matching_date_entries[0].source, "known_future")

    def test_recurrence_without_a_known_amount_is_skipped_not_guessed(self):
        state = make_state(balance="0", minimum="0", recurring_expense_patterns=[
            make_pattern(category="rent", direction="debit", amount=None, next_occurrence="2024-01-05"),
        ])
        result = build_forecast(state, START, horizon_days=45)
        self.assertEqual(result.entries, ())
        self.assertTrue(any("no known typical amount" in w for w in result.warnings))


# ---------------------------------------------------------------------------
# 9-10, 19 (pending credit half): pending transactions
# ---------------------------------------------------------------------------


class PendingTransactionTests(unittest.TestCase):
    def test_pending_expense_reduces_balance_on_settlement_date(self):
        state = make_state(balance="1000", minimum="0", pending_events=[
            make_normalized_event("p1", direction="debit", amount="200", status="pending", event_date="2024-01-12"),
        ])
        result = build_forecast(state, START)
        by_date = dict(result.balance_timeline)
        self.assertEqual(by_date[D("2024-01-12")], Decimal("800"))

    def test_overdue_pending_expense_is_clamped_to_start_date(self):
        # event_date is BEFORE start_date -- treat it as an immediate obligation.
        state = make_state(balance="1000", minimum="0", pending_events=[
            make_normalized_event("p1", direction="debit", amount="200", status="pending", event_date="2023-12-01"),
        ])
        result = build_forecast(state, START)
        self.assertEqual(result.entries[0].date, START)

    def test_pending_credit_is_excluded_from_forecast(self):
        state = make_state(balance="100", minimum="50", pending_events=[
            make_normalized_event("p1", direction="credit", amount="10000", status="pending", event_date="2024-01-05"),
        ])
        result = build_forecast(state, START)
        self.assertEqual(result.entries, ())
        self.assertIn("p1", result.excluded_pending_income_event_ids)
        self.assertEqual(result.minimum_projected_balance, Decimal("100"))


# ---------------------------------------------------------------------------
# 11-12: failed / cancelled events never projected
# ---------------------------------------------------------------------------


class ExcludedStatusTests(unittest.TestCase):
    def test_failed_and_cancelled_events_are_never_projected(self):
        state = make_state(balance="1000", minimum="0", cancelled_or_failed_events=[
            make_normalized_event("c1", direction="debit", amount="9999", status="cancelled", event_date="2024-01-05"),
            make_normalized_event("f1", direction="debit", amount="9999", status="failed", event_date="2024-01-06"),
        ])
        result = build_forecast(state, START)
        self.assertEqual(result.entries, ())
        self.assertTrue(result.is_safe)


# ---------------------------------------------------------------------------
# 13, 18 (part): unresolved amounts
# ---------------------------------------------------------------------------


class UnresolvedAmountTests(unittest.TestCase):
    def test_unresolved_relevant_event_is_flagged_not_zeroed(self):
        state = make_state(balance="1000", minimum="0", unresolved_amount_events=[
            make_normalized_event("u1", direction="debit", amount=None, status="scheduled", event_date="2024-01-15"),
        ])
        result = build_forecast(state, START)
        self.assertEqual(result.entries, ())  # contributes no amount
        self.assertIn("u1", result.unresolved_relevant_event_ids)
        self.assertTrue(result.has_unresolved_evidence)

    def test_historical_unresolved_amount_does_not_flag_the_forecast(self):
        state = make_state(balance="1000", minimum="0", unresolved_amount_events=[
            make_normalized_event("u1", direction="credit", amount=None, status="settled", event_date="2023-01-01"),
        ])
        result = build_forecast(state, START)
        self.assertFalse(result.has_unresolved_evidence)
        self.assertEqual(result.unresolved_relevant_event_ids, ())


# ---------------------------------------------------------------------------
# 14-16, 22: hypothetical payment safety
# ---------------------------------------------------------------------------


class PaymentSafetyTests(unittest.TestCase):
    def test_payment_safe_today_stays_safe_through_horizon(self):
        state = make_state(balance="10000", minimum="1000")
        result = can_safely_pay(state, START, START, Decimal("2000"))
        self.assertTrue(result.is_safe)
        self.assertEqual(result.minimum_projected_balance, Decimal("8000"))

    def test_payment_causing_future_violation_is_rejected(self):
        # Safe-looking today, but a later known rent payment breaches the floor.
        state = make_state(balance="1000", minimum="100", future_expense_events=[
            make_normalized_event("rent1", direction="debit", amount="850", category="rent",
                                   status="scheduled", event_date="2024-02-01"),
        ])
        result = can_safely_pay(state, START, START, Decimal("100"))
        self.assertFalse(result.is_safe)
        self.assertEqual(result.first_violation_date, D("2024-02-01"))

    def test_payment_becomes_safe_after_future_income(self):
        state = make_state(balance="100", minimum="50", future_income_events=[
            make_normalized_event("sal1", direction="credit", amount="5000", category="salary",
                                   status="scheduled", event_date="2024-01-20"),
        ])
        too_early = can_safely_pay(state, START, START, Decimal("500"))
        # Same-day netting means the income IS usable on its own date
        # (see SameDayOrderingTests).
        on_income_date = can_safely_pay(state, START, D("2024-01-20"), Decimal("500"))
        self.assertFalse(too_early.is_safe)
        self.assertTrue(on_income_date.is_safe)

    def test_temporary_violation_is_detected_even_if_balance_recovers_later(self):
        state = make_state(
            balance="12000", minimum="10000",
            future_expense_events=[make_normalized_event("e1", direction="debit", amount="4000",
                                                           status="scheduled", event_date="2024-01-11")],
            future_income_events=[make_normalized_event("i1", direction="credit", amount="7000",
                                                          status="scheduled", event_date="2024-01-12")],
        )
        result = build_forecast(state, START)
        # Day 10: 12000 -> Day 11: 8000 (violation) -> Day 12: 15000 (recovered)
        self.assertFalse(result.is_safe)
        self.assertEqual(result.first_violation_date, D("2024-01-11"))
        self.assertEqual(result.minimum_projected_balance, Decimal("8000"))


# ---------------------------------------------------------------------------
# 17-20: earliest_safe_payment_date / maximum_safe_payment
# ---------------------------------------------------------------------------


class SafeDateAndAmountTests(unittest.TestCase):
    def test_earliest_safe_payment_date_returns_first_valid_date(self):
        state = make_state(balance="100", minimum="50", future_income_events=[
            make_normalized_event("sal1", direction="credit", amount="1000", status="scheduled", event_date="2024-02-01"),
        ])
        earliest = earliest_safe_payment_date(state, START, Decimal("500"))
        # The credit posts 02-01 and is usable the same day it posts
        # (same-day netting) -- see SameDayOrderingTests.
        self.assertEqual(earliest, D("2024-02-01"))

    def test_earliest_safe_payment_date_returns_none_when_never_safe(self):
        state = make_state(balance="100", minimum="50")
        earliest = earliest_safe_payment_date(state, START, Decimal("100000"))
        self.assertIsNone(earliest)

    def test_earliest_safe_payment_date_is_clipped_to_deadline_when_given(self):
        state = make_state(balance="100", minimum="50", future_income_events=[
            make_normalized_event("sal1", direction="credit", amount="1000", status="scheduled", event_date="2024-02-15"),
        ])
        within_deadline = earliest_safe_payment_date(state, START, Decimal("500"), deadline=D("2024-03-01"))
        before_income = earliest_safe_payment_date(state, START, Decimal("500"), deadline=D("2024-01-31"))
        self.assertEqual(within_deadline, D("2024-02-15"))  # usable the same day it posts
        self.assertIsNone(before_income, "payment date after the deadline must not be returned")

    def test_maximum_safe_payment_respects_the_floor(self):
        state = make_state(balance="5000", minimum="1000")
        max_amount = maximum_safe_payment(state, START, START)
        self.assertEqual(max_amount, Decimal("4000"))
        # Paying exactly the max must be (barely) safe; one more must not be.
        self.assertTrue(can_safely_pay(state, START, START, max_amount).is_safe)
        self.assertFalse(can_safely_pay(state, START, START, max_amount + Decimal("0.01")).is_safe)

    def test_maximum_safe_payment_accounts_for_a_future_obligation(self):
        state = make_state(balance="5000", minimum="1000", future_expense_events=[
            make_normalized_event("e1", direction="debit", amount="3000", status="scheduled", event_date="2024-01-20"),
        ])
        max_amount = maximum_safe_payment(state, START, START)
        # Headroom today (4000) is NOT the answer -- the 3000 obligation 20 days
        # later means the true safe amount is bounded by that future point too.
        self.assertEqual(max_amount, Decimal("1000"))

    def test_maximum_safe_payment_is_never_negative(self):
        state = make_state(balance="500", minimum="1000")  # already below floor
        max_amount = maximum_safe_payment(state, START, START)
        self.assertEqual(max_amount, Decimal("0"))

    def test_maximum_safe_payment_is_deterministic(self):
        state = make_state(balance="5000", minimum="1000", future_expense_events=[
            make_normalized_event("e1", direction="debit", amount="500", status="scheduled", event_date="2024-01-15"),
        ])
        first = maximum_safe_payment(state, START, START)
        second = maximum_safe_payment(state, START, START)
        self.assertEqual(first, second)


# ---------------------------------------------------------------------------
# Multi-payment simulation (simulate_payments / prior_payments) -- added to
# support Phase 4's partial-payment/installment verification, which must
# check a whole plan together rather than payment-by-payment.
# ---------------------------------------------------------------------------


class MultiPaymentSimulationTests(unittest.TestCase):
    def test_two_individually_safe_payments_can_combine_to_be_unsafe(self):
        # Each of these two payments alone leaves the balance at 600 (>= the
        # 500 floor), but making BOTH leaves only 200 -- must be flagged.
        state = make_state(balance="1600", minimum="500")
        alone_1 = simulate_payment(state, START, START, Decimal("1000"))
        alone_2 = simulate_payment(state, START, D("2024-01-10"), Decimal("1000"))
        self.assertTrue(alone_1.is_safe)
        self.assertTrue(alone_2.is_safe)

        combined = simulate_payments(state, START, [(START, Decimal("1000")), (D("2024-01-10"), Decimal("1000"))])
        self.assertFalse(combined.is_safe)
        self.assertEqual(combined.minimum_projected_balance, Decimal("-400"))
        self.assertEqual(len(combined.hypothetical_entries), 2)

    def test_maximum_safe_payment_accounts_for_prior_payments(self):
        state = make_state(balance="5000", minimum="1000")
        # With no prior commitment, up to 4000 is safe.
        self.assertEqual(maximum_safe_payment(state, START, START), Decimal("4000"))
        # With 3000 already committed on the same day, only 1000 remains.
        remaining = maximum_safe_payment(state, START, START, prior_payments=[(START, Decimal("3000"))])
        self.assertEqual(remaining, Decimal("1000"))

    def test_earliest_safe_payment_date_accounts_for_prior_payments(self):
        state = make_state(balance="1000", minimum="0", future_income_events=[
            make_normalized_event("sal1", direction="credit", amount="5000", status="scheduled", event_date="2024-01-20"),
        ])
        # Without a prior commitment, 800 is safe immediately.
        self.assertEqual(earliest_safe_payment_date(state, START, Decimal("800")), START)
        # With 800 already committed today, an additional 800 isn't safe
        # until the salary posts (same-day netting makes it usable that
        # same day, not the day after -- see SameDayOrderingTests).
        earliest_remaining = earliest_safe_payment_date(
            state, START, Decimal("800"), prior_payments=[(START, Decimal("800"))])
        self.assertEqual(earliest_remaining, D("2024-01-20"))

    def test_simulate_payments_rejects_a_payment_before_start_date(self):
        state = make_state()
        with self.assertRaises(ValueError):
            simulate_payments(state, START, [(START - timedelta(days=1), Decimal("10"))])


# ---------------------------------------------------------------------------
# 21: same-day ordering + the two explicit same-day synthetic scenarios
# ---------------------------------------------------------------------------


class SameDayOrderingTests(unittest.TestCase):
    def test_same_day_ordering_is_deterministic_across_repeated_runs(self):
        state = make_state(balance="1000", minimum="0", future_expense_events=[
            make_normalized_event("e1", direction="debit", amount="100", category="b", status="scheduled", event_date="2024-01-05"),
            make_normalized_event("e2", direction="debit", amount="50", category="a", status="scheduled", event_date="2024-01-05"),
        ], future_income_events=[
            make_normalized_event("i1", direction="credit", amount="200", category="salary", status="scheduled", event_date="2024-01-05"),
        ])
        first = build_forecast(state, START)
        second = build_forecast(state, START)
        self.assertEqual([e.event_id for e in first.entries], [e.event_id for e in second.entries])

    def test_same_day_salary_does_rescue_a_same_day_payment(self):
        # A salary arrives on the SAME day as the candidate payment date.
        # Per the documented same-day rule (all of a day's flows are
        # netted together and checked once, at end-of-day -- confirmed
        # against sample_requests.csv's request_19, whose own solved
        # answer pays its second installment exactly on a salary date and
        # treats it as safe), the salary DOES cover the same-day payment.
        state = make_state(balance="10", minimum="0", future_income_events=[
            make_normalized_event("sal1", direction="credit", amount="1000", status="scheduled", event_date="2024-01-10"),
        ])
        day_before = can_safely_pay(state, START, D("2024-01-09"), Decimal("500"))
        same_day = can_safely_pay(state, START, D("2024-01-10"), Decimal("500"))
        self.assertFalse(day_before.is_safe, "the salary hasn't posted yet the day before")
        self.assertTrue(same_day.is_safe, "the salary is available the same day it posts")

    def test_same_day_expense_stacks_with_the_hypothetical_payment(self):
        # A real debit landing on the same day as the hypothetical payment
        # must still reduce the balance -- it is not "overwritten" by the
        # hypothetical payment's same-day sort position.
        state = make_state(balance="1000", minimum="0", future_expense_events=[
            make_normalized_event("bill1", direction="debit", amount="400", status="scheduled", event_date="2024-01-10"),
        ])
        result = can_safely_pay(state, START, D("2024-01-10"), Decimal("500"))
        self.assertEqual(result.minimum_projected_balance, Decimal("100"))  # 1000 - 500 - 400
        self.assertTrue(result.is_safe)  # exactly at the (zero) floor


# ---------------------------------------------------------------------------
# 23-24: desired completion date
# ---------------------------------------------------------------------------


class DeadlineTests(unittest.TestCase):
    def test_safe_by_deadline_reflects_the_deadline(self):
        state = make_state(balance="0", minimum="0", future_income_events=[
            make_normalized_event("sal1", direction="credit", amount="1000", status="scheduled", event_date="2024-01-20"),
        ])
        profile_ok = describe_payment_safety(state, START, Decimal("500"), deadline=D("2024-02-01"))
        profile_late = describe_payment_safety(state, START, Decimal("500"), deadline=D("2024-01-10"))
        self.assertFalse(profile_ok.safe_now)
        self.assertEqual(profile_ok.first_safe_date, D("2024-01-20"))  # usable the same day it posts
        self.assertTrue(profile_ok.safe_by_deadline)
        self.assertFalse(profile_late.safe_by_deadline)

    def test_first_safe_date_is_computed_over_the_full_horizon_not_clipped_by_deadline(self):
        # Part 16: earliest_date_for_full_payment must be independent of
        # payment-method preference/options/spending changes -- and of the
        # deadline itself; describe_payment_safety must report the TRUE
        # first_safe_date even when it falls after the deadline.
        state = make_state(balance="0", minimum="0", future_income_events=[
            make_normalized_event("sal1", direction="credit", amount="1000", status="scheduled", event_date="2024-03-01"),
        ])
        profile = describe_payment_safety(state, START, Decimal("500"), deadline=D("2024-01-15"))
        self.assertEqual(profile.first_safe_date, D("2024-03-01"))  # usable the same day it posts
        self.assertFalse(profile.safe_by_deadline)


# ---------------------------------------------------------------------------
# 26: forecast horizon
# ---------------------------------------------------------------------------


class HorizonTests(unittest.TestCase):
    def test_forecast_horizon_is_start_date_plus_90_days_by_default(self):
        state = make_state()
        result = build_forecast(state, START)
        self.assertEqual(result.forecast_start_date, START)
        self.assertEqual(result.forecast_end_date, START + timedelta(days=90))

    def test_forecast_horizon_is_configurable(self):
        state = make_state()
        result = build_forecast(state, START, horizon_days=30)
        self.assertEqual(result.forecast_end_date, START + timedelta(days=30))


# ---------------------------------------------------------------------------
# 27-30: provenance, no method preference, no spending changes, currency
# ---------------------------------------------------------------------------


class ApiContractTests(unittest.TestCase):
    def test_entries_retain_source_event_ids_and_pattern_ids(self):
        state = make_state(balance="0", minimum="0",
            future_expense_events=[make_normalized_event("known1", direction="debit", amount="10",
                                                           status="scheduled", event_date="2024-01-05")],
            recurring_expense_patterns=[make_pattern(category="rent", direction="debit", amount="500",
                                                       next_occurrence="2024-02-01", source_ids=("ev_a", "ev_b"))],
        )
        result = build_forecast(state, START, horizon_days=45)
        known = next(e for e in result.entries if e.source == "known_future")
        recurring = next(e for e in result.entries if e.source == "recurring_expense")
        self.assertEqual(known.event_id, "known1")
        self.assertEqual(recurring.pattern_source_event_ids, ("ev_a", "ev_b"))

    def test_no_payment_method_preference_field_is_read_by_forecast(self):
        # Two states differing ONLY in payment_methods_user_will_consider
        # (and max_installment_months) must produce an identical forecast.
        base_kwargs = dict(balance="1000", minimum="100", future_expense_events=[
            make_normalized_event("e1", direction="debit", amount="200", status="scheduled", event_date="2024-01-15"),
        ])
        state_a = make_state(payment_methods_user_will_consider=["full_payment"], max_installment_months=None, **base_kwargs)
        state_b = make_state(payment_methods_user_will_consider=["installments"], max_installment_months=6, **base_kwargs)
        result_a = can_safely_pay(state_a, START, START, Decimal("300"))
        result_b = can_safely_pay(state_b, START, START, Decimal("300"))
        self.assertEqual(result_a.is_safe, result_b.is_safe)
        self.assertEqual(result_a.minimum_projected_balance, result_b.minimum_projected_balance)

    def test_no_spending_change_semantics_exist_on_a_forecast_entry(self):
        # ForecastEntry has no field for stopping/reducing an expense --
        # spending-change optimization is out of scope for this phase.
        field_names = ForecastEntry.__dataclass_fields__.keys()
        self.assertNotIn("stopped", field_names)
        self.assertNotIn("reduced_to", field_names)


class RealDatasetForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not default_dataset_dir().exists():
            raise unittest.SkipTest("dataset/ not found; skipping integration tests")
        cls.store = DataStore.load(strict=True)
        cls.converter = CurrencyConverter.from_data_store(cls.store)

    def test_builds_a_forecast_for_every_user_without_crashing(self):
        for user_id, request in [(r.user_id, r) for r in self.store.requests[:40]]:
            state = build_financial_state(self.store, self.converter, user_id)
            result = build_forecast(state, request.request_date)
            self.assertEqual(result.forecast_start_date, request.request_date)

    def test_foreign_currency_future_income_is_already_home_currency_in_the_forecast(self):
        # user_25 has USD salary against an IDR home currency (Phase 0/2).
        state = build_financial_state(self.store, self.converter, "user_25")
        if not state.future_income_events:
            self.skipTest("user_25 has no future income event in the current dataset snapshot")
        result = build_forecast(state, D("2024-01-01"), horizon_days=3650)
        known_income_entries = [e for e in result.entries if e.source == "known_future" and e.direction == "credit"]
        self.assertTrue(known_income_entries)
        # amounts must be plain IDR magnitudes (thousands+), not raw USD figures
        self.assertTrue(all(e.amount > Decimal("1000") for e in known_income_entries))

    def test_sample_request_01_full_payment_is_safe_on_request_date(self):
        # request_01: solved sample says affordable_now/full_payment, amount 25256 ZAR.
        sample = self.store.get_sample_request("request_01")
        state = build_financial_state(self.store, self.converter, sample.user_id)
        result = can_safely_pay(state, sample.request_date, sample.request_date, sample.amount_safe_to_pay)
        self.assertTrue(result.is_safe)


if __name__ == "__main__":
    unittest.main()
