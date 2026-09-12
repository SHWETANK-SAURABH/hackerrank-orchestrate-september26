"""Spending-change candidates (Phase 5, Part 13-20).

Extends the Phase 4 plan system with exactly two allowed operations --
`stop:event_id` and `reduce_to:event_id:new_amount` -- applied only to a
recurring expense's representative event, never invented, never applied
to income, a one-off historical expense, or a protected/inflexible
category. At most 3 changes may appear in one plan.

Design mirrors `evidence.py`'s overlay pattern: `apply_spending_changes`
never mutates the FinancialState it's given, it returns a new one with
`recurring_expense_patterns` adjusted (a stopped pattern removed from
future projection entirely, a reduced one given its new amount) --
historical `effective_events` are always left completely untouched
(Part 15/16: "do not erase/alter historical transactions").

Spending changes are only ever *offered*, never forced: `payment_plans`
and `decision_engine` still prefer a plan with zero changes whenever one
already works (Part 19), and `decision_engine`'s ranking keeps "no
spending changes" as an explicit tie-break criterion within a tier
(Part 20). Whether a spending-change plan can reach a *stronger* tier
than any no-change plan (the `request_06` scenario) is decided by the
status hierarchy itself, before ranking ever runs -- see
`decision_engine.py`'s module docstring.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations
from typing import List, Optional, Sequence, Tuple

import forecast
from data_loader import Request
from financial_state import FinancialState, RecurrencePattern
from payment_plans import PaymentPlan

MAX_SPENDING_CHANGES = 3


@dataclass(frozen=True)
class SpendingChange:
    kind: str  # "stop" | "reduce_to" -- Part 13: exactly these two, nothing else
    event_id: str
    new_amount: Optional[Decimal] = None  # only meaningful for "reduce_to"

    def to_output_string(self) -> str:
        if self.kind == "stop":
            return f"stop:{self.event_id}"
        return f"reduce_to:{self.event_id}:{self.new_amount}"

    @staticmethod
    def parse(text: str) -> Optional["SpendingChange"]:
        """Inverse of `to_output_string` -- used by the verifier to
        independently re-validate whatever string ended up on a
        PaymentPlan, regardless of how it was produced. Returns None for
        anything not exactly one of the two allowed shapes (Part 13)."""
        if text.startswith("stop:"):
            event_id = text[len("stop:"):]
            return SpendingChange("stop", event_id) if event_id else None
        if text.startswith("reduce_to:"):
            rest = text[len("reduce_to:"):]
            parts = rest.split(":")
            if len(parts) != 2:
                return None
            event_id, amount_text = parts
            try:
                amount = Decimal(amount_text)
            except Exception:  # noqa: BLE001 -- any parse failure means "not a valid change"
                return None
            return SpendingChange("reduce_to", event_id, amount) if event_id else None
        return None


@dataclass(frozen=True)
class SpendingChangeValidation:
    valid: bool
    errors: Tuple[str, ...]
    pattern: Optional[RecurrencePattern]


def find_pattern_for_event(state: FinancialState, event_id: str) -> Optional[RecurrencePattern]:
    """The event_id in a spending change must be the recurrence pattern's
    own representative/source event (Part 17) -- never a synthetic
    recurrence id, since none exists in this dataset's schema."""
    for pattern in state.recurring_expense_patterns:
        if event_id in pattern.source_event_ids:
            return pattern
    return None


def validate_spending_change(
    state: FinancialState, change: SpendingChange, all_changes: Sequence[SpendingChange],
) -> SpendingChangeValidation:
    """Independently re-derive every eligibility rule from Part 14 --
    never trust that a candidate's own construction already checked them."""
    errors: List[str] = []

    if change.kind not in ("stop", "reduce_to"):
        return SpendingChangeValidation(False, (f"unsupported spending-change kind {change.kind!r}",), None)

    pattern = find_pattern_for_event(state, change.event_id)
    if pattern is None:
        errors.append(f"{change.event_id} is not the representative event of any recurring expense pattern")
        return SpendingChangeValidation(False, tuple(errors), None)

    if pattern.category in state.expense_categories_to_protect:
        errors.append(f"category {pattern.category!r} is protected and cannot be changed")

    if change.kind == "stop":
        if pattern.flexibility not in ("stoppable", "reducible_or_stoppable"):
            errors.append(f"{change.event_id} (flexibility={pattern.flexibility!r}) cannot be stopped")
        if pattern.category not in state.expense_categories_user_is_willing_to_stop:
            errors.append(f"user profile does not permit stopping category {pattern.category!r}")
    else:  # reduce_to
        if pattern.flexibility not in ("reducible", "reducible_or_stoppable"):
            errors.append(f"{change.event_id} (flexibility={pattern.flexibility!r}) cannot be reduced")
        if pattern.category not in state.expense_categories_user_is_willing_to_reduce:
            errors.append(f"user profile does not permit reducing category {pattern.category!r}")
        if change.new_amount is None or change.new_amount < 0:
            errors.append("reduce_to requires a non-negative new_amount")
        else:
            minimum_allowed = pattern.minimum_allowed_amount if pattern.minimum_allowed_amount is not None else Decimal(0)
            if change.new_amount < minimum_allowed:
                errors.append(f"new_amount {change.new_amount} is below minimum_allowed_amount {minimum_allowed}")
            if pattern.typical_amount_home_currency is not None and change.new_amount >= pattern.typical_amount_home_currency:
                errors.append(
                    f"new_amount {change.new_amount} does not reduce below the normal amount "
                    f"{pattern.typical_amount_home_currency}"
                )

    # Part 14 rule 8 / Part 33: stop and reduce are mutually exclusive on
    # the same event, and no event may appear twice in one plan.
    for other in all_changes:
        if other is not change and other.event_id == change.event_id:
            errors.append(f"event {change.event_id} is targeted by more than one spending change in the same plan")

    return SpendingChangeValidation(not errors, tuple(errors), pattern)


def validate_spending_changes(state: FinancialState, changes: Sequence[SpendingChange]) -> Tuple[bool, List[str]]:
    if len(changes) > MAX_SPENDING_CHANGES:
        return False, [f"at most {MAX_SPENDING_CHANGES} spending changes are allowed, got {len(changes)}"]
    all_errors: List[str] = []
    for change in changes:
        result = validate_spending_change(state, change, changes)
        all_errors.extend(result.errors)
    return not all_errors, all_errors


def apply_spending_changes(state: FinancialState, changes: Sequence[SpendingChange]) -> FinancialState:
    """A new FinancialState with `changes` applied to
    `recurring_expense_patterns` only -- `effective_events` (history) is
    never touched (Part 15: "do not erase the historical event").
    A `stop` removes the pattern from future projection entirely; a
    `reduce_to` lowers its typical amount. Unrelated patterns pass
    through unchanged (Part 16: "do not alter unrelated occurrences")."""
    if not changes:
        return state
    change_by_event = {c.event_id: c for c in changes}
    new_patterns: List[RecurrencePattern] = []
    for pattern in state.recurring_expense_patterns:
        matched = next((c for eid, c in change_by_event.items() if eid in pattern.source_event_ids), None)
        if matched is None:
            new_patterns.append(pattern)
        elif matched.kind == "stop":
            continue  # dropped -- no future occurrences projected
        else:
            new_patterns.append(replace(pattern, typical_amount_home_currency=matched.new_amount))
    return replace(state, recurring_expense_patterns=new_patterns)


# ---------------------------------------------------------------------------
# Candidate generation (Part 18/19/21/22)
# ---------------------------------------------------------------------------


def eligible_spending_change_options(state: FinancialState, horizon_end: date) -> List[SpendingChange]:
    """One candidate change per eligible recurring expense pattern (Part
    14): relevant to the future forecast (has a projected occurrence
    before the horizon ends), not protected, and permitted by the user's
    profile. A `reduce_to` offers the pattern's own `minimum_allowed_amount`
    -- the largest permitted cash-flow impact for that lever -- since
    ranking (never this function) is what decides whether using it is
    actually worthwhile (Part 19: "the objective is not cut as much
    spending as possible")."""
    options: List[SpendingChange] = []
    for pattern in state.recurring_expense_patterns:
        if not pattern.source_event_ids:
            continue
        if pattern.next_expected_occurrence is None or pattern.next_expected_occurrence > horizon_end:
            continue
        if pattern.category in state.expense_categories_to_protect:
            continue
        representative_event_id = pattern.source_event_ids[-1]
        if (pattern.flexibility in ("stoppable", "reducible_or_stoppable")
                and pattern.category in state.expense_categories_user_is_willing_to_stop):
            options.append(SpendingChange("stop", representative_event_id))
        if (pattern.flexibility in ("reducible", "reducible_or_stoppable")
                and pattern.category in state.expense_categories_user_is_willing_to_reduce
                and pattern.typical_amount_home_currency is not None):
            minimum_allowed = pattern.minimum_allowed_amount if pattern.minimum_allowed_amount is not None else Decimal(0)
            if minimum_allowed < pattern.typical_amount_home_currency:
                options.append(SpendingChange("reduce_to", representative_event_id, minimum_allowed))
    return options


def _combinations_up_to(items: Sequence[SpendingChange], max_size: int):
    """Combinations of size 1, 2, ... max_size, smallest first -- so the
    caller trying each in order naturally finds the fewest-changes
    solution first (Part 19), without generating every combination
    blindly when a small one already works (Part 18's pruning ask)."""
    for size in range(1, max_size + 1):
        for combo in combinations(items, size):
            yield combo


def generate_spending_change_candidates(
    request: Request, state: FinancialState,
) -> List[PaymentPlan]:
    """Only meaningful when a plain (zero-change) full payment isn't
    already safe today -- callers should check that first (Part 19: "do
    not change spending unless actually necessary"). Tries full-payment-
    today first, since that is the exact scenario Part 21 (`request_06`)
    and Part 22 (`request_11`) both describe: a spending change makes the
    *full* request payable today when it otherwise wasn't."""
    if "full_payment" not in state.payment_methods_user_will_consider:
        return []
    horizon_end = request.request_date + timedelta(days=forecast.DEFAULT_HORIZON_DAYS)
    eligible = eligible_spending_change_options(state, horizon_end)
    if not eligible:
        return []

    for combo in _combinations_up_to(eligible, MAX_SPENDING_CHANGES):
        adjusted_state = apply_spending_changes(state, combo)
        result = forecast.can_safely_pay(adjusted_state, request.request_date, request.request_date, request.requested_amount)
        if result.is_safe:
            return [PaymentPlan(
                method="full_payment",
                payment_dates=(request.request_date,),
                payment_amounts=(request.requested_amount,),
                number_of_payments=1,
                total_payable=request.requested_amount,
                financing_fee=Decimal(0),
                full_payment_date=request.request_date,
                spending_changes=tuple(c.to_output_string() for c in combo),
            )]
    return []
