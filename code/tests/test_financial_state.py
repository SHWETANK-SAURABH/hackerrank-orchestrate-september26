"""Tests for code/financial_state.py.

Uses a minimal ``FakeStore`` duck-typed to the three DataStore methods
financial_state.py actually calls (get_profile / get_events_for_user /
get_event), so fixtures are built directly as dataclasses -- no synthetic
CSV files needed here (contrast with Phase 1's fixture approach, which
had to exercise CSV-level schema/integrity failures).
"""

import sys
import unittest
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from currency import CurrencyConverter  # noqa: E402
from data_loader import DataStore, ExchangeRate, FinancialEvent, FinancialProfile, default_dataset_dir  # noqa: E402
from financial_state import (  # noqa: E402
    ConflictTier,
    EventTreatment,
    LinkedRelationship,
    RecurrenceFrequency,
    build_financial_state,
    classify_treatment,
    resolve_conflict,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


class FakeStore:
    """Duck-typed stand-in for DataStore, backed by in-memory objects."""

    def __init__(self, profiles: List[FinancialProfile], events: List[FinancialEvent]):
        self._profiles: Dict[str, FinancialProfile] = {p.user_id: p for p in profiles}
        self._events_by_id: Dict[str, FinancialEvent] = {e.event_id: e for e in events}
        self._events_by_user: Dict[str, List[FinancialEvent]] = defaultdict(list)
        for e in events:
            self._events_by_user[e.user_id].append(e)

    def get_profile(self, user_id: str) -> Optional[FinancialProfile]:
        return self._profiles.get(user_id)

    def get_events_for_user(self, user_id: str) -> List[FinancialEvent]:
        return list(self._events_by_user.get(user_id, []))

    def get_event(self, event_id: str) -> Optional[FinancialEvent]:
        return self._events_by_id.get(event_id)


def make_profile(user_id="user_1", home_currency="USD", balance="1000", min_balance="100", **overrides):
    fields = dict(
        user_id=user_id,
        home_currency=home_currency,
        current_available_balance=Decimal(balance),
        minimum_balance_to_keep=Decimal(min_balance),
        financial_priorities=["emergency_savings"],
        expense_categories_to_protect=["rent"],
        expense_categories_user_is_willing_to_reduce=["dining"],
        expense_categories_user_is_willing_to_stop=["streaming"],
        payment_methods_user_will_consider=["full_payment"],
        max_installment_months=None,
    )
    fields.update(overrides)
    return FinancialProfile(**fields)


def make_event(
    event_id,
    user_id="user_1",
    event_type="expense",
    description="Test event",
    category="shopping",
    direction="debit",
    amount="100",
    currency="USD",
    event_date="2024-01-01",
    settlement_date="2024-01-01",
    status="settled",
    linked_event_id=None,
    flexibility="fixed",
    minimum_allowed_amount=None,
):
    return FinancialEvent(
        event_id=event_id,
        user_id=user_id,
        event_type=event_type,
        description=description,
        category=category,
        direction=direction,
        amount=Decimal(amount) if amount is not None else None,
        currency=currency,
        event_date=date.fromisoformat(event_date),
        settlement_date=date.fromisoformat(settlement_date) if settlement_date else None,
        status=status,
        linked_event_id=linked_event_id,
        flexibility=flexibility,
        minimum_allowed_amount=Decimal(minimum_allowed_amount) if minimum_allowed_amount is not None else None,
    )


EUR_TO_USD_RATE = ExchangeRate(date(2024, 1, 5), "EUR", "USD", Decimal("1.1"))


def build_state(events, profile=None, rates=None):
    profile = profile or make_profile()
    store = FakeStore([profile], events)
    converter = CurrencyConverter(rates or [EUR_TO_USD_RATE])
    return build_financial_state(store, converter, profile.user_id)


# ---------------------------------------------------------------------------
# 1-3: currency normalization + missing amounts
# ---------------------------------------------------------------------------


class CurrencyAndAmountTests(unittest.TestCase):
    def test_home_currency_event_remains_unchanged(self):
        ev = make_event("event_1", amount="250.50", currency="USD")
        state = build_state([ev])
        normalized = state.effective_events[0]
        self.assertEqual(normalized.amount_home_currency, Decimal("250.50"))

    def test_foreign_currency_event_is_converted_using_settlement_date(self):
        # event_date and settlement_date deliberately differ; the rate only
        # exists for the settlement date, so using event_date would fail.
        ev = make_event(
            "event_1", amount="100", currency="EUR",
            event_date="2024-01-01", settlement_date="2024-01-05",
        )
        state = build_state([ev])
        normalized = state.effective_events[0]
        self.assertEqual(normalized.amount_home_currency, Decimal("100") * Decimal("1.1"))

    def test_missing_amount_remains_none(self):
        ev = make_event("event_1", amount=None, status="settled")
        state = build_state([ev])
        self.assertEqual(len(state.unresolved_amount_events), 1)
        normalized = state.unresolved_amount_events[0]
        self.assertIsNone(normalized.original_amount)
        self.assertIsNone(normalized.amount_home_currency)
        self.assertEqual(normalized.treatment, EventTreatment.UNRESOLVED_AMOUNT)

    def test_no_unresolved_blank_amount_is_converted_to_zero(self):
        ev = make_event("event_1", amount=None)
        state = build_state([ev])
        normalized = state.unresolved_amount_events[0]
        self.assertIsNone(normalized.amount_home_currency)
        self.assertNotEqual(normalized.amount_home_currency, Decimal("0"))


# ---------------------------------------------------------------------------
# 4-7: status-based classification
# ---------------------------------------------------------------------------


class StatusClassificationTests(unittest.TestCase):
    def test_failed_expense_does_not_become_effective(self):
        ev = make_event("event_1", direction="debit", status="failed", amount="500")
        state = build_state([ev])
        self.assertEqual(len(state.effective_events), 0)
        self.assertEqual(len(state.cancelled_or_failed_events), 1)
        self.assertEqual(state.cancelled_or_failed_events[0].treatment, EventTreatment.FAILED)

    def test_cancelled_transaction_does_not_become_effective_cash_flow(self):
        ev = make_event("event_1", direction="debit", status="cancelled", amount="500")
        state = build_state([ev])
        self.assertEqual(len(state.effective_events), 0)
        self.assertEqual(state.cancelled_or_failed_events[0].treatment, EventTreatment.CANCELLED)

    def test_pending_credit_is_not_considered_available_cash(self):
        ev = make_event("event_1", direction="credit", status="pending", amount="300", event_type="refund")
        state = build_state([ev])
        self.assertEqual(len(state.effective_events), 0)
        self.assertEqual(len(state.pending_events), 1)
        self.assertEqual(state.pending_events[0].treatment, EventTreatment.PENDING_INCOME)

    def test_future_scheduled_income_is_future_income_not_current_cash(self):
        ev = make_event("event_1", direction="credit", status="scheduled", amount="5000", event_type="income")
        state = build_state([ev])
        self.assertEqual(len(state.effective_events), 0)
        self.assertEqual(len(state.future_income_events), 1)
        self.assertEqual(state.future_income_events[0].treatment, EventTreatment.SCHEDULED_INCOME)

    def test_classify_treatment_covers_every_observed_status_direction_combo(self):
        # Sanity sweep over the combinations Phase 0 confirmed actually
        # exist in dataset/financial_events.csv.
        combos = [
            ("settled", "credit", EventTreatment.EFFECTIVE_INCOME),
            ("settled", "debit", EventTreatment.EFFECTIVE_EXPENSE),
            ("pending", "credit", EventTreatment.PENDING_INCOME),
            ("pending", "debit", EventTreatment.PENDING_EXPENSE),
            ("scheduled", "credit", EventTreatment.SCHEDULED_INCOME),
            ("scheduled", "debit", EventTreatment.SCHEDULED_EXPENSE),
            ("cancelled", "debit", EventTreatment.CANCELLED),
            ("failed", "debit", EventTreatment.FAILED),
            ("unrealized", "non_cash", EventTreatment.NON_CASH),
        ]
        for status, direction, expected in combos:
            ev = make_event("event_x", status=status, direction=direction, amount="10")
            self.assertEqual(classify_treatment(ev, []), expected, f"{status}/{direction}")

    def test_unrecognized_combo_is_flagged_not_guessed(self):
        ev = make_event("event_1", status="disputed", direction="debit", amount="10")
        warnings: List[str] = []
        treatment = classify_treatment(ev, warnings)
        self.assertEqual(treatment, EventTreatment.UNKNOWN)
        self.assertTrue(any("disputed" in w for w in warnings))


# ---------------------------------------------------------------------------
# 8-10: linked events
# ---------------------------------------------------------------------------


class LinkedEventTests(unittest.TestCase):
    def test_linked_event_is_resolved_through_event_id(self):
        original = make_event("event_1", event_type="expense", status="settled", amount="500", event_date="2024-01-01")
        refund = make_event(
            "event_2", event_type="refund", status="settled", direction="credit",
            amount="500", event_date="2024-01-10", linked_event_id="event_1",
        )
        state = build_state([original, refund])
        refund_normalized = next(n for n in state.all_normalized_events if n.event_id == "event_2")
        self.assertIsNotNone(refund_normalized.linked_info)
        self.assertIs(refund_normalized.linked_info.original, original)
        self.assertEqual(refund_normalized.linked_info.relationship, LinkedRelationship.REFUND_OF_EXPENSE)
        self.assertEqual(refund_normalized.linked_info.issues, ())

    def test_invalid_linked_event_id_is_detected(self):
        ev = make_event("event_2", linked_event_id="event_does_not_exist")
        state = build_state([ev])
        normalized = state.effective_events[0]
        self.assertIsNotNone(normalized.linked_info)
        self.assertIsNone(normalized.linked_info.original)
        self.assertIn("missing_reference", normalized.linked_info.issues)
        self.assertTrue(any("does not exist" in w for w in state.warnings))
        self.assertTrue(any("event_does_not_exist" in issue for issue in state.linked_event_issues))

    def test_cross_user_linked_event_id_is_detected(self):
        other_user_event = make_event("event_1", user_id="user_2", status="settled", amount="500")
        current = make_event("event_2", user_id="user_1", linked_event_id="event_1")
        store = FakeStore([make_profile("user_1"), make_profile("user_2")], [other_user_event, current])
        converter = CurrencyConverter([EUR_TO_USD_RATE])
        state = build_financial_state(store, converter, "user_1")
        normalized = state.effective_events[0]
        self.assertIn("cross_user_reference", normalized.linked_info.issues)
        self.assertTrue(any("different user" in w for w in state.warnings))

    def test_out_of_order_linked_reference_is_detected(self):
        # The "original" happens AFTER the event that links to it -- suspicious.
        current = make_event("event_2", event_date="2024-01-01", linked_event_id="event_1")
        later_original = make_event("event_1", event_date="2024-02-01", status="settled")
        state = build_state([later_original, current])
        normalized = next(n for n in state.all_normalized_events if n.event_id == "event_2")
        self.assertIn("out_of_order_reference", normalized.linked_info.issues)

    def test_tricky_linked_scenario_naive_void_of_original_would_be_wrong(self):
        """A settled expense linked FROM a pending refund. If code naively
        assumed "linked_event_id present => the earlier event is voided",
        it would drop a real EUR 500 expense from the ledger. The correct
        behavior (this test) is that the original expense stays fully
        effective, and the refund is only PENDING (not yet available cash)
        until it settles -- exactly mirroring the 8 real
        expense(settled)->refund(pending) pairs found in the dataset during
        Phase 0 reconnaissance.
        """
        expense = make_event("event_100", event_type="expense", status="settled", amount="500", event_date="2024-01-01")
        pending_refund = make_event(
            "event_101", event_type="refund", direction="credit", status="pending",
            amount="500", event_date="2024-01-15", linked_event_id="event_100",
        )
        state = build_state([expense, pending_refund])

        effective_ids = {n.event_id for n in state.effective_events}
        pending_ids = {n.event_id for n in state.pending_events}

        self.assertIn("event_100", effective_ids, "the original settled expense must stay effective")
        self.assertNotIn("event_100", pending_ids)
        self.assertIn("event_101", pending_ids, "an unsettled refund must not be counted as available cash")
        self.assertNotIn("event_101", effective_ids)

        # No conflict-resolution record should have been created for this
        # pair -- a refund is an independent fact, not a competing
        # description of the same slot as its original expense.
        self.assertEqual(state.conflict_resolutions, [])


# ---------------------------------------------------------------------------
# Conflict resolution (unit tests on resolve_conflict + integration via
# the two "same slot" real-world patterns)
# ---------------------------------------------------------------------------


class ConflictResolutionTests(unittest.TestCase):
    def test_tier1_explicit_status_beats_estimate(self):
        pending = make_event("event_1", status="pending", event_date="2024-01-01", amount="100")
        settled = make_event("event_2", status="settled", event_date="2024-01-01", amount="100")
        winner, tier, losers = resolve_conflict([pending, settled])
        self.assertIs(winner, settled)
        self.assertEqual(tier, ConflictTier.EXPLICIT_STATUS)
        self.assertEqual(losers, [pending])

    def test_tier2_newer_record_wins_among_equally_definitive_candidates(self):
        # settlement_date=None so _effective_date falls back to the (deliberately
        # different) event_date rather than both defaulting to the same day.
        older = make_event("event_1", status="cancelled", event_date="2024-01-01", settlement_date=None, amount="100")
        newer = make_event("event_2", status="cancelled", event_date="2024-02-01", settlement_date=None, amount="100")
        winner, tier, _ = resolve_conflict([older, newer])
        self.assertIs(winner, newer)
        self.assertEqual(tier, ConflictTier.NEWER_SAME_SOURCE)

    def test_tier3_settled_beats_scheduled_at_the_same_date(self):
        scheduled = make_event("event_1", status="scheduled", event_date="2024-01-01", amount="100")
        settled = make_event("event_2", status="settled", event_date="2024-01-01", amount="100")
        # Both are "definitive-or-not": settled is definitive, scheduled is
        # not, so tier 1 alone would already resolve this -- verify it does.
        winner, tier, _ = resolve_conflict([scheduled, settled])
        self.assertIs(winner, settled)
        self.assertEqual(tier, ConflictTier.EXPLICIT_STATUS)

    def test_tier4_safer_interpretation_prefers_larger_debit(self):
        small = make_event("event_1", status="pending", direction="debit", event_date="2024-01-01", amount="50")
        large = make_event("event_2", status="pending", direction="debit", event_date="2024-01-01", amount="500")
        winner, tier, _ = resolve_conflict([small, large])
        self.assertIs(winner, large)
        self.assertEqual(tier, ConflictTier.SAFER_INTERPRETATION)

    def test_tier4_safer_interpretation_prefers_smaller_credit(self):
        small = make_event("event_1", status="pending", direction="credit", event_date="2024-01-01", amount="50")
        large = make_event("event_2", status="pending", direction="credit", event_date="2024-01-01", amount="500")
        winner, tier, _ = resolve_conflict([small, large])
        self.assertIs(winner, small)
        self.assertEqual(tier, ConflictTier.SAFER_INTERPRETATION)

    def test_conflict_resolution_is_deterministic_across_repeated_calls(self):
        candidates = [
            make_event("event_1", status="pending", event_date="2024-01-01", amount="100"),
            make_event("event_2", status="settled", event_date="2024-01-01", amount="100"),
        ]
        first = resolve_conflict(list(candidates))
        second = resolve_conflict(list(candidates))
        self.assertEqual(first[0].event_id, second[0].event_id)
        self.assertEqual(first[1], second[1])

    def test_single_candidate_is_trivially_its_own_winner(self):
        only = make_event("event_1")
        winner, _, losers = resolve_conflict([only])
        self.assertIs(winner, only)
        self.assertEqual(losers, [])

    def test_empty_candidate_list_raises(self):
        with self.assertRaises(ValueError):
            resolve_conflict([])

    def test_resolve_conflict_on_the_real_cancelled_then_replaced_shape(self):
        # Real pattern from Phase 0 (user_01's event_100 -> event_101):
        # a cancelled authorization followed by the actual settled charge,
        # both settling on the same date. Settled and cancelled are
        # equally "definitive" under tier 1, and tier 2 (newer date) is a
        # tie at this shared settlement date, so tier 3 resolves it:
        # settled beats a non-settled record -- agreeing with what each
        # row's own independent EventTreatment already gives.
        cancelled = make_event(
            "event_100", event_type="expense", status="cancelled", amount="816.2",
            event_date="2024-02-14", settlement_date="2024-02-16",
        )
        replacement = make_event(
            "event_101", event_type="expense", status="settled", amount="816.2",
            event_date="2024-02-16", settlement_date="2024-02-16", linked_event_id="event_100",
        )
        winner, tier, losers = resolve_conflict([cancelled, replacement])
        self.assertEqual(winner.event_id, "event_101")
        self.assertEqual(tier, ConflictTier.SETTLED_OVER_ESTIMATE)
        self.assertEqual([c.event_id for c in losers], ["event_100"])

        # build_financial_state does NOT invoke resolve_conflict for this
        # relationship (see resolve_conflict's docstring) -- it doesn't
        # need to, since each row's own status already gives the right
        # per-row treatment. Confirm no conflict-resolution record appears.
        state = build_state([cancelled, replacement])
        self.assertEqual(state.conflict_resolutions, [])
        by_id = {n.event_id: n for n in state.all_normalized_events}
        self.assertEqual(by_id["event_100"].treatment, EventTreatment.CANCELLED)
        self.assertEqual(by_id["event_101"].treatment, EventTreatment.EFFECTIVE_EXPENSE)

    def test_resolve_conflict_would_misjudge_the_failed_then_rescheduled_pair(self):
        """Documents *why* build_financial_state never calls resolve_conflict
        for a failed-attempt -> rescheduled-retry pair (the other real linked
        pattern from Phase 0, e.g. user_55's event_5168 -> event_5169).

        'failed' and 'scheduled' are NOT equally definitive under tier 1
        (failed is an explicit, closed outcome; scheduled is a mere
        estimate of the future) -- so resolve_conflict, applied naively
        here, declares the FAILED attempt the "winner". That would be the
        wrong takeaway if it were used to decide what belongs in the
        ledger: the live, forward-looking fact is the rescheduled
        payment, not the dead attempt. The two records are independently
        true sequential facts, not competing descriptions of one fact, so
        there is nothing for resolve_conflict to correctly adjudicate here
        -- which is exactly why financial_state.py leaves both rows to
        their own independent, correct EventTreatment instead.
        """
        failed = make_event(
            "event_1", event_type="debt_payment", category="debt_repayment",
            status="failed", amount="1000", event_date="2024-01-01",
        )
        rescheduled = make_event(
            "event_2", event_type="debt_payment", category="debt_repayment",
            status="scheduled", amount="1000", event_date="2024-01-08", linked_event_id="event_1",
        )
        winner, tier, _ = resolve_conflict([failed, rescheduled])
        self.assertEqual(winner.event_id, "event_1")  # the misleading "winner" if misapplied
        self.assertEqual(tier, ConflictTier.EXPLICIT_STATUS)

        # What actually lands in the ledger is correct regardless, because
        # build_financial_state never calls resolve_conflict for this pair:
        state = build_state([failed, rescheduled])
        self.assertEqual(state.conflict_resolutions, [])
        by_id = {n.event_id: n for n in state.all_normalized_events}
        self.assertEqual(by_id["event_1"].treatment, EventTreatment.FAILED)
        self.assertEqual(by_id["event_2"].treatment, EventTreatment.SCHEDULED_EXPENSE)


# ---------------------------------------------------------------------------
# 11-13: recurrence detection
# ---------------------------------------------------------------------------


class RecurrenceDetectionTests(unittest.TestCase):
    def _monthly_events(self, event_id_prefix, event_type, category, direction, amount, n=4, start="2024-01-05"):
        start_date = date.fromisoformat(start)
        events = []
        for i in range(n):
            # simple, deterministic ~30 day cadence
            d = date(start_date.year + (start_date.month - 1 + i) // 12,
                      (start_date.month - 1 + i) % 12 + 1,
                      start_date.day)
            events.append(make_event(
                f"{event_id_prefix}_{i}", event_type=event_type, category=category,
                direction=direction, amount=amount, event_date=d.isoformat(),
                settlement_date=d.isoformat(), status="settled",
            ))
        return events

    def test_recurring_income_pattern_is_detected_with_clear_repeated_evidence(self):
        salary_events = self._monthly_events("salary", "income", "salary", "credit", "5000", n=5)
        state = build_state(salary_events)
        self.assertEqual(len(state.recurring_income_patterns), 1)
        pattern = state.recurring_income_patterns[0]
        self.assertEqual(pattern.frequency, RecurrenceFrequency.MONTHLY)
        self.assertEqual(pattern.typical_amount_home_currency, Decimal("5000"))
        self.assertEqual(pattern.occurrence_count, 5)
        self.assertEqual(set(pattern.source_event_ids), {e.event_id for e in salary_events})

    def test_recurring_expense_pattern_is_detected_with_clear_repeated_evidence(self):
        rent_events = self._monthly_events("rent", "expense", "rent", "debit", "1200", n=6)
        state = build_state(rent_events)
        self.assertEqual(len(state.recurring_expense_patterns), 1)
        pattern = state.recurring_expense_patterns[0]
        self.assertEqual(pattern.frequency, RecurrenceFrequency.MONTHLY)
        self.assertEqual(pattern.typical_amount_home_currency, Decimal("1200"))

    def test_one_off_transaction_is_not_classified_as_recurring(self):
        one_off = make_event("event_1", event_type="expense", category="shopping", status="settled", amount="300")
        state = build_state([one_off])
        self.assertEqual(state.recurring_expense_patterns, [])
        self.assertEqual(state.recurring_income_patterns, [])

    def test_two_occurrences_are_not_enough_to_be_recurring(self):
        two = self._monthly_events("shopping", "expense", "shopping", "debit", "300", n=2)
        state = build_state(two)
        self.assertEqual(state.recurring_expense_patterns, [])

    def test_irregular_spacing_is_not_classified_as_recurring(self):
        irregular = [
            make_event("event_1", category="shopping", amount="100", event_date="2024-01-01", status="settled"),
            make_event("event_2", category="shopping", amount="100", event_date="2024-01-04", status="settled"),
            make_event("event_3", category="shopping", amount="100", event_date="2024-06-20", status="settled"),
        ]
        state = build_state(irregular)
        self.assertEqual(state.recurring_expense_patterns, [])

    def test_cancelled_occurrences_are_not_used_as_recurrence_evidence(self):
        # 2 settled + 1 cancelled "look like" 3 occurrences, but the
        # cancelled one never happened and must not count as evidence.
        events = self._monthly_events("subs", "subscription", "streaming", "debit", "20", n=2)
        events.append(make_event(
            "subs_extra", event_type="subscription", category="streaming", direction="debit",
            amount="20", status="cancelled", event_date="2024-03-05",
        ))
        state = build_state(events)
        self.assertEqual(state.recurring_expense_patterns, [])


# ---------------------------------------------------------------------------
# 14-16: policy/profile field preservation
# ---------------------------------------------------------------------------


class PolicyPreservationTests(unittest.TestCase):
    def test_flexibility_and_minimum_allowed_amount_are_preserved(self):
        ev = make_event("event_1", category="dining", flexibility="reducible", minimum_allowed_amount="50", amount="200")
        state = build_state([ev])
        normalized = state.effective_events[0]
        self.assertEqual(normalized.flexibility, "reducible")
        self.assertEqual(normalized.minimum_allowed_amount, Decimal("50"))

    def test_protected_reducible_stoppable_categories_are_preserved(self):
        profile = make_profile(
            expense_categories_to_protect=["rent", "groceries"],
            expense_categories_user_is_willing_to_reduce=["dining", "shopping"],
            expense_categories_user_is_willing_to_stop=["streaming", "gym"],
        )
        state = build_state([make_event("event_1")], profile=profile)
        self.assertEqual(state.expense_categories_to_protect, ["rent", "groceries"])
        self.assertEqual(state.expense_categories_user_is_willing_to_reduce, ["dining", "shopping"])
        self.assertEqual(state.expense_categories_user_is_willing_to_stop, ["streaming", "gym"])

    def test_current_profile_balance_remains_the_starting_balance(self):
        profile = make_profile(balance="12345.67", min_balance="500")
        big_expense = make_event("event_1", amount="99999", status="settled")
        state = build_state([big_expense], profile=profile)
        # Historical events must NOT be netted into the starting balance in
        # this phase -- it always equals the profile's reported value.
        self.assertEqual(state.current_available_balance, Decimal("12345.67"))
        self.assertEqual(state.minimum_balance_to_keep, Decimal("500"))


# ---------------------------------------------------------------------------
# Real-dataset smoke checks (skipped if dataset/ is unavailable)
# ---------------------------------------------------------------------------


class RealDatasetFinancialStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not default_dataset_dir().exists():
            raise unittest.SkipTest("dataset/ not found; skipping integration tests")
        cls.store = DataStore.load(strict=True)
        cls.converter = CurrencyConverter.from_data_store(cls.store)

    def test_builds_state_for_every_user_without_error(self):
        for user_id in self.store.profiles_by_user_id:
            state = build_financial_state(self.store, self.converter, user_id)
            self.assertEqual(state.user_id, user_id)

    def test_balance_matches_profile_not_reconstructed_from_events(self):
        profile = self.store.get_profile("user_01")
        state = build_financial_state(self.store, self.converter, "user_01")
        self.assertEqual(state.current_available_balance, profile.current_available_balance)

    def test_blank_amount_events_land_in_unresolved_bucket_not_zeroed(self):
        # event_253 (Phase 0/1): user_03's blank-amount August 2019 salary.
        state = build_financial_state(self.store, self.converter, "user_03")
        unresolved_ids = {n.event_id for n in state.unresolved_amount_events}
        self.assertIn("event_253", unresolved_ids)
        normalized = next(n for n in state.unresolved_amount_events if n.event_id == "event_253")
        self.assertIsNone(normalized.amount_home_currency)

    def test_foreign_currency_salary_is_normalized_for_user_25(self):
        state = build_financial_state(self.store, self.converter, "user_25")
        foreign = [n for n in state.effective_events if n.original_currency != state.home_currency]
        self.assertTrue(foreign, "expected at least one foreign-currency effective event for user_25")
        for n in foreign:
            self.assertIsNotNone(n.amount_home_currency)

    def test_at_least_one_real_user_has_a_detected_recurring_pattern(self):
        state = build_financial_state(self.store, self.converter, "user_01")
        self.assertTrue(state.recurring_income_patterns or state.recurring_expense_patterns)

    def test_real_linked_replacement_pair_is_labelled_without_forcing_a_conflict_winner(self):
        # event_100 (cancelled) -> event_101 (settled) for user_01, found during Phase 0.
        # Both rows keep their own correct treatment; no conflict_resolutions
        # entry is produced for this dataset (see resolve_conflict's docstring
        # for why forcing one would be the wrong design here).
        state = build_financial_state(self.store, self.converter, "user_01")
        by_id = {n.event_id: n for n in state.all_normalized_events}
        self.assertEqual(by_id["event_100"].treatment, EventTreatment.CANCELLED)
        self.assertEqual(by_id["event_101"].treatment, EventTreatment.EFFECTIVE_EXPENSE)
        self.assertEqual(
            by_id["event_101"].linked_info.relationship,
            LinkedRelationship.REPLACEMENT_AFTER_CANCELLATION,
        )
        self.assertEqual(state.conflict_resolutions, [])

    def test_no_real_user_produces_a_conflict_resolution_record(self):
        # Phase 0/2 finding: none of the dataset's 58 linked_event_id pairs
        # are genuine same-fact conflicts (see resolve_conflict's docstring).
        for user_id in self.store.profiles_by_user_id:
            state = build_financial_state(self.store, self.converter, user_id)
            self.assertEqual(state.conflict_resolutions, [], user_id)


if __name__ == "__main__":
    unittest.main()
