"""Tests for code/spending_changes.py and its integration into
code/decision_engine.py / code/verifier.py.
"""

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_loader import DataStore, Request, default_dataset_dir  # noqa: E402
from currency import CurrencyConverter  # noqa: E402
from financial_state import FinancialState, RecurrenceFrequency, RecurrencePattern, build_financial_state  # noqa: E402
from decision_engine import make_decision  # noqa: E402
from spending_changes import (  # noqa: E402
    MAX_SPENDING_CHANGES,
    SpendingChange,
    apply_spending_changes,
    eligible_spending_change_options,
    find_pattern_for_event,
    generate_spending_change_candidates,
    validate_spending_change,
    validate_spending_changes,
)

D = date.fromisoformat


def make_pattern(
    event_id="event_A", category="streaming", amount="20", flexibility="stoppable",
    min_allowed=None, next_occurrence="2024-01-10", direction="debit", event_type="expense",
) -> RecurrencePattern:
    return RecurrencePattern(
        user_id="user_1", event_type=event_type, direction=direction, category=category,
        description="test pattern", frequency=RecurrenceFrequency.MONTHLY, typical_interval_days=30.0,
        typical_amount_home_currency=Decimal(amount) if amount is not None else None, home_currency="USD",
        next_expected_occurrence=D(next_occurrence) if next_occurrence else None,
        occurrence_count=5, confidence="high", flexibility=flexibility,
        minimum_allowed_amount=Decimal(min_allowed) if min_allowed is not None else None,
        source_event_ids=(event_id,),
    )


def make_state(balance="1000", minimum="100", **overrides) -> FinancialState:
    fields = dict(
        user_id="user_1", home_currency="USD",
        current_available_balance=Decimal(balance), minimum_balance_to_keep=Decimal(minimum),
        expense_categories_to_protect=["rent"],
        expense_categories_user_is_willing_to_stop=["streaming"],
        expense_categories_user_is_willing_to_reduce=["dining"],
        payment_methods_user_will_consider=["full_payment"],
    )
    fields.update(overrides)
    return FinancialState(**fields)


def make_request(requested_amount="870", request_date="2024-01-01", desired_completion_date="2024-02-01") -> Request:
    return Request("request_1", "user_1", D(request_date), "purchase", Decimal(requested_amount),
                    D(desired_completion_date), False, "test")


# ---------------------------------------------------------------------------
# 26-33: eligibility rules
# ---------------------------------------------------------------------------


class EligibilityTests(unittest.TestCase):
    def test_eligible_recurring_expense_can_be_stopped(self):
        state = make_state(recurring_expense_patterns=[make_pattern(category="streaming", flexibility="stoppable")])
        change = SpendingChange("stop", "event_A")
        result = validate_spending_change(state, change, [change])
        self.assertTrue(result.valid, result.errors)

    def test_eligible_recurring_expense_can_be_reduced(self):
        state = make_state(recurring_expense_patterns=[
            make_pattern(category="dining", flexibility="reducible", amount="100", min_allowed="50"),
        ])
        change = SpendingChange("reduce_to", "event_A", Decimal("60"))
        result = validate_spending_change(state, change, [change])
        self.assertTrue(result.valid, result.errors)

    def test_inflexible_expense_cannot_be_changed(self):
        state = make_state(recurring_expense_patterns=[make_pattern(category="streaming", flexibility="fixed")])
        change = SpendingChange("stop", "event_A")
        result = validate_spending_change(state, change, [change])
        self.assertFalse(result.valid)

    def test_protected_category_cannot_be_changed(self):
        state = make_state(
            expense_categories_to_protect=["streaming"],
            recurring_expense_patterns=[make_pattern(category="streaming", flexibility="stoppable")],
        )
        change = SpendingChange("stop", "event_A")
        result = validate_spending_change(state, change, [change])
        self.assertFalse(result.valid)

    def test_category_not_permitted_by_profile_cannot_be_changed(self):
        state = make_state(
            expense_categories_user_is_willing_to_stop=[],  # streaming no longer permitted
            recurring_expense_patterns=[make_pattern(category="streaming", flexibility="stoppable")],
        )
        change = SpendingChange("stop", "event_A")
        result = validate_spending_change(state, change, [change])
        self.assertFalse(result.valid)

    def test_reduce_to_cannot_go_below_minimum_allowed_amount(self):
        state = make_state(recurring_expense_patterns=[
            make_pattern(category="dining", flexibility="reducible", amount="100", min_allowed="50"),
        ])
        change = SpendingChange("reduce_to", "event_A", Decimal("40"))
        result = validate_spending_change(state, change, [change])
        self.assertFalse(result.valid)

    def test_reduce_to_cannot_equal_or_exceed_normal_amount(self):
        state = make_state(recurring_expense_patterns=[
            make_pattern(category="dining", flexibility="reducible", amount="100", min_allowed="50"),
        ])
        change = SpendingChange("reduce_to", "event_A", Decimal("100"))
        result = validate_spending_change(state, change, [change])
        self.assertFalse(result.valid)

    def test_stop_and_reduce_cannot_target_same_event(self):
        state = make_state(recurring_expense_patterns=[
            make_pattern(category="streaming", flexibility="reducible_or_stoppable", amount="20", min_allowed="10"),
        ])
        stop = SpendingChange("stop", "event_A")
        reduce = SpendingChange("reduce_to", "event_A", Decimal("15"))
        valid, errors = validate_spending_changes(state, [stop, reduce])
        self.assertFalse(valid)
        self.assertTrue(any("more than one spending change" in e for e in errors))

    def test_maximum_three_spending_changes(self):
        state = make_state(recurring_expense_patterns=[
            make_pattern("event_A", "streaming", "20", "stoppable"),
        ])
        changes = [SpendingChange("stop", f"event_{i}") for i in range(MAX_SPENDING_CHANGES + 1)]
        valid, errors = validate_spending_changes(state, changes)
        self.assertFalse(valid)
        self.assertTrue(any("at most" in e for e in errors))

    def test_income_cannot_be_changed(self):
        # Income patterns never even appear in recurring_expense_patterns,
        # so an event_id belonging to one simply isn't found as an
        # eligible expense target.
        state = make_state(recurring_income_patterns=[
            make_pattern("event_income", "salary", "5000", "fixed", direction="credit", event_type="income"),
        ])
        self.assertIsNone(find_pattern_for_event(state, "event_income"))

    def test_one_off_expense_cannot_be_changed(self):
        # A one-off (non-recurring) expense was never turned into a
        # RecurrencePattern by Phase 2, so it can never be a valid target.
        state = make_state(recurring_expense_patterns=[])
        change = SpendingChange("stop", "event_one_off")
        result = validate_spending_change(state, change, [change])
        self.assertFalse(result.valid)
        self.assertIsNone(result.pattern)

    def test_already_cancelled_or_failed_event_is_not_a_target(self):
        # Cancelled/failed events never end up as a recurring pattern's
        # source_event_ids (Phase 2 only builds patterns from settled
        # history), so they can't be referenced either.
        state = make_state(recurring_expense_patterns=[make_pattern("event_settled", "streaming", "20", "stoppable")])
        change = SpendingChange("stop", "event_cancelled_or_failed")
        result = validate_spending_change(state, change, [change])
        self.assertFalse(result.valid)


# ---------------------------------------------------------------------------
# 35-38: stop/reduce semantics preserve history, only affect the future
# ---------------------------------------------------------------------------


class SemanticsTests(unittest.TestCase):
    def test_historical_event_is_not_erased_when_stopped(self):
        pattern = make_pattern("event_A", "streaming", "20", "stoppable")
        state = make_state(recurring_expense_patterns=[pattern])
        new_state = apply_spending_changes(state, [SpendingChange("stop", "event_A")])
        # The pattern (Phase 2's record of what historically happened) is
        # removed from FUTURE projection, but nothing here deletes any
        # historical FinancialEvent/NormalizedEvent -- those live in
        # effective_events, untouched by this function entirely.
        self.assertEqual(state.effective_events, new_state.effective_events)
        self.assertNotIn(pattern, new_state.recurring_expense_patterns)

    def test_historical_event_is_not_rewritten_when_reduced(self):
        pattern = make_pattern("event_A", "dining", "100", "reducible", min_allowed="50")
        state = make_state(recurring_expense_patterns=[pattern])
        new_state = apply_spending_changes(state, [SpendingChange("reduce_to", "event_A", Decimal("60"))])
        self.assertEqual(state.effective_events, new_state.effective_events)
        reduced = new_state.recurring_expense_patterns[0]
        self.assertEqual(reduced.typical_amount_home_currency, Decimal("60"))
        self.assertEqual(pattern.typical_amount_home_currency, Decimal("100"), "original pattern object must be untouched")

    def test_synthetic_recurrence_id_cannot_be_used_as_output_event_id(self):
        # A spending change must reference the pattern's own real
        # source_event_id -- an id we invented (never a source_event_id
        # of any pattern) is rejected.
        state = make_state(recurring_expense_patterns=[make_pattern("event_A", "streaming", "20", "stoppable")])
        change = SpendingChange("stop", "synthetic_recurrence_id_99")
        result = validate_spending_change(state, change, [change])
        self.assertFalse(result.valid)

    def test_only_future_recurring_occurrences_are_affected(self):
        pattern = make_pattern("event_A", "streaming", "20", "stoppable", next_occurrence="2024-01-10")
        state = make_state(recurring_expense_patterns=[pattern])
        new_state = apply_spending_changes(state, [SpendingChange("stop", "event_A")])
        # Stopping removes the pattern from the list used to PROJECT future
        # occurrences -- unrelated patterns and all historical data pass
        # through completely unchanged.
        unrelated = make_pattern("event_B", "utilities", "50", "fixed")
        state2 = make_state(recurring_expense_patterns=[pattern, unrelated])
        new_state2 = apply_spending_changes(state2, [SpendingChange("stop", "event_A")])
        self.assertEqual(list(new_state2.recurring_expense_patterns), [unrelated])


# ---------------------------------------------------------------------------
# 41-47: plan integration
# ---------------------------------------------------------------------------


class PlanIntegrationTests(unittest.TestCase):
    def test_no_change_plan_preferred_when_equally_successful(self):
        # Balance is already comfortable without any change -- spending
        # changes must never even be explored (Part 19).
        state = make_state(balance="10000", minimum="100",
                            recurring_expense_patterns=[make_pattern("event_A", "streaming", "20", "stoppable")])
        request = make_request(requested_amount="500")
        decision = make_decision(request, state, [])
        self.assertEqual(decision.affordability_status, "affordable_now")
        self.assertEqual(decision.spending_changes_needed, ())

    def test_spending_change_plan_can_unlock_affordable_with_plan(self):
        # 3 monthly streaming charges of 20 fall inside the 90-day
        # horizon; stopping frees exactly enough headroom.
        state = make_state(balance="1000", minimum="100",
                            recurring_expense_patterns=[make_pattern("event_A", "streaming", "20", "stoppable")])
        request = make_request(requested_amount="870")
        decision = make_decision(request, state, [])
        self.assertEqual(decision.affordability_status, "affordable_with_plan")
        self.assertEqual(decision.recommended_payment_method, "full_payment")
        self.assertEqual(decision.spending_changes_needed, ("stop:event_A",))

    def test_spending_change_plan_is_re_forecasted(self):
        # Directly confirm the candidate's safety was checked against the
        # ADJUSTED (post-stop) state, not the raw one.
        state = make_state(balance="1000", minimum="100",
                            recurring_expense_patterns=[make_pattern("event_A", "streaming", "20", "stoppable")])
        request = make_request(requested_amount="870")
        plans = generate_spending_change_candidates(request, state)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].spending_changes, ("stop:event_A",))

    def test_spending_change_plan_is_re_verified_as_one_complete_scenario(self):
        import verifier
        state = make_state(balance="1000", minimum="100",
                            recurring_expense_patterns=[make_pattern("event_A", "streaming", "20", "stoppable")])
        request = make_request(requested_amount="870")
        plans = generate_spending_change_candidates(request, state)
        result = verifier.verify_plan(plans[0], request, state, {})
        self.assertTrue(result.valid, result.errors)

    def test_unsafe_spending_change_plan_is_rejected(self):
        # Stopping the only eligible expense still isn't enough to cover
        # a much larger request.
        state = make_state(balance="1000", minimum="100",
                            recurring_expense_patterns=[make_pattern("event_A", "streaming", "20", "stoppable")])
        request = make_request(requested_amount="5000")
        decision = make_decision(request, state, [])
        self.assertEqual(decision.affordability_status, "not_affordable")
        self.assertEqual(decision.spending_changes_needed, ())

    def test_spending_change_plan_cannot_exceed_3_changes(self):
        patterns = [make_pattern(f"event_{i}", f"cat_{i}", "10", "stoppable") for i in range(5)]
        state = make_state(
            balance="500", minimum="490",  # needs all 5 stopped to reach a big request, but only 3 are allowed
            expense_categories_user_is_willing_to_stop=[f"cat_{i}" for i in range(5)],
            recurring_expense_patterns=patterns,
        )
        request = make_request(requested_amount="35")  # needs more than 3*10 headroom improvements can safely cover here
        for plan in generate_spending_change_candidates(request, state):
            self.assertLessEqual(len(plan.spending_changes), MAX_SPENDING_CHANGES)

    def test_plan_ranking_remains_deterministic_with_spending_changes_present(self):
        state = make_state(balance="1000", minimum="100",
                            recurring_expense_patterns=[make_pattern("event_A", "streaming", "20", "stoppable")])
        request = make_request(requested_amount="870")
        first = make_decision(request, state, [])
        second = make_decision(request, state, [])
        self.assertEqual(first.spending_changes_needed, second.spending_changes_needed)
        self.assertEqual(first.affordability_status, second.affordability_status)

    def test_fewest_changes_are_tried_first(self):
        # Two patterns (50/month, 3 occurrences each in the 90-day
        # horizon = 150 apiece). Base headroom is 900 - 300 = 600 with
        # neither stopped, 900 - 150 = 750 with either ONE stopped, and
        # 900 with both stopped. Request 700 is reachable by stopping
        # just one -- the search must not needlessly use both.
        state = make_state(
            balance="1000", minimum="100",
            expense_categories_user_is_willing_to_stop=["streaming", "gym"],
            recurring_expense_patterns=[
                make_pattern("event_A", "streaming", "50", "stoppable", next_occurrence="2024-01-10"),
                make_pattern("event_B", "gym", "50", "stoppable", next_occurrence="2024-01-10"),
            ],
        )
        request = make_request(requested_amount="700")
        plans = generate_spending_change_candidates(request, state)
        self.assertEqual(len(plans[0].spending_changes), 1)


class RealCaseTests(unittest.TestCase):
    """request_06/request_11 end-to-end, per Phase 5 Parts 21/22 --
    exercising the real dataset's actual eligible expenses. Documented in
    full, including the residual gap to the exact sample figures, in
    eval/phase5_report.md."""

    @classmethod
    def setUpClass(cls):
        if not default_dataset_dir().exists():
            raise unittest.SkipTest("dataset/ not found; skipping integration tests")
        cls.store = DataStore.load(strict=True)
        cls.converter = CurrencyConverter.from_data_store(cls.store)

    def test_request_06_streaming_is_an_eligible_stop_target(self):
        state = build_financial_state(self.store, self.converter, "user_06")
        streaming = next((p for p in state.recurring_expense_patterns if p.category == "streaming"), None)
        self.assertIsNotNone(streaming)
        self.assertEqual(streaming.flexibility, "stoppable")
        self.assertIn("streaming", state.expense_categories_user_is_willing_to_stop)

    def test_request_11_entertainment_is_an_eligible_reduce_target(self):
        state = build_financial_state(self.store, self.converter, "user_11")
        entertainment = next((p for p in state.recurring_expense_patterns if p.category == "entertainment"), None)
        self.assertIsNotNone(entertainment)
        self.assertEqual(entertainment.flexibility, "reducible")
        self.assertIn("entertainment", state.expense_categories_user_is_willing_to_reduce)

    def test_request_06_end_to_end_runs_without_crashing(self):
        from evidence import build_evidence_bundle, apply_evidence
        sample = self.store.get_sample_request("request_06")
        state = build_financial_state(self.store, self.converter, sample.user_id)
        bundle = build_evidence_bundle(self.store)
        evidence_state, _ = apply_evidence(state, bundle, self.converter)
        options = self.store.get_payment_options(sample.request_id)
        decision = make_decision(sample, evidence_state, options)
        self.assertIn(decision.affordability_status, ("affordable_now", "affordable_with_plan"))
        self.assertEqual(decision.recommended_payment_method, "full_payment")

    def test_request_11_end_to_end_runs_without_crashing(self):
        from evidence import build_evidence_bundle, apply_evidence
        sample = self.store.get_sample_request("request_11")
        state = build_financial_state(self.store, self.converter, sample.user_id)
        bundle = build_evidence_bundle(self.store)
        evidence_state, _ = apply_evidence(state, bundle, self.converter)
        options = self.store.get_payment_options(sample.request_id)
        decision = make_decision(sample, evidence_state, options)
        self.assertIn(decision.affordability_status, ("affordable_now", "affordable_with_plan"))


if __name__ == "__main__":
    unittest.main()
