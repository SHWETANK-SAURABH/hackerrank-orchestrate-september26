"""Deterministic buy/wait decision logic (Phase 4, extended in Phase 5
with spending-change candidates).

Turns Phase 3's raw safety facts plus the verified candidate plans from
`payment_plans.py`/`spending_changes.py` into a final, deterministic
decision: `affordability_status`, `recommended_payment_method`, the
chosen `PaymentPlan`, and the two facts that must never depend on any of
that -- `amount_safe_to_pay` and `earliest_date_for_full_payment`
(Part 16 of Phase 4), both computed directly from Phase 3, independent of
payment-method preference, supplied installment options, spending
changes, and this module's own ranking.

**The status hierarchy is resolved first, then ranked within a tier**
(Phase 4 Part 11/12) -- never one flat comparison across every candidate.
The concrete counter-example that makes this non-negotiable: sample
`request_06` is `affordable_with_plan/full_payment` because a spending
change lets the user pay in full *today*, even though the *unassisted*
earliest full-payment date is later. Concretely: a zero-spending-change
`full_payment` candidate is the only thing that can earn tier 1
(`affordable_now`); a `full_payment` candidate that only works *with* a
spending change is graded into tier 2 (`affordable_with_plan`) alongside
partial payment and installments (Phase 5 Part 20/21) -- so a flat
ranking that always preferred "no spending changes" first could never
produce this exact sample's answer, because the comparison must never
happen between tiers.

Explicitly out of scope: messages/images beyond what `evidence.py`
already resolved before this function is called, and polished natural-
language explanations (Phase 6+).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

import forecast
import payment_plans
import spending_changes
import verifier
from data_loader import PaymentOption, Request
from financial_state import FinancialState
from payment_plans import PaymentPlan
from verifier import VerificationResult

#: The only allowed output values (Part 1) -- never invent additional ones.
AFFORDABILITY_STATUSES = ("affordable_now", "affordable_with_plan", "affordable_later", "not_affordable")
PAYMENT_METHODS = ("full_payment", "partial_payment", "installments", "wait", "not_recommended")


@dataclass(frozen=True)
class VerifiedCandidate:
    plan: PaymentPlan
    verification: VerificationResult


@dataclass(frozen=True)
class DecisionResult:
    request_id: str
    amount_safe_to_pay: Decimal
    affordability_status: str
    recommended_payment_method: str
    #: None only when recommended_payment_method == "not_recommended".
    payment_plan: Optional[PaymentPlan]
    earliest_date_for_full_payment: Optional[date]
    #: Populated from the recommended plan's own `spending_changes`
    #: (Phase 5) -- `()` whenever the chosen plan needed none.
    spending_changes_needed: Tuple[str, ...] = ()
    #: Full diagnostic trail: every generated candidate and its
    #: independent verification result, kept for audit/debugging and the
    #: sample cross-check -- never consulted to make the decision itself
    #: (the decision only ever looks at candidates whose verification.valid is True).
    candidates: Tuple[VerifiedCandidate, ...] = ()


def _payment_option_sort_key(option_id: Optional[str]) -> Tuple[int, int]:
    """Numeric (not lexicographic) ordering of payment_option_N ids, so
    "payment_option_2" sorts before "payment_option_10". A plan with no
    option id (partial_payment) sorts after any plan that has one --
    this criterion is the ranking's last-resort tie-break (Part 13 #6)
    and only ever matters when two candidates are otherwise identical on
    every earlier criterion."""
    if option_id is None:
        return (1, 0)
    match = re.search(r"(\d+)$", option_id)
    return (0, int(match.group(1)) if match else 0)


def _rank_key(plan: PaymentPlan) -> Tuple:
    """The problem statement's 6-point preference order (Part 13), as a
    deterministic sort key. Criterion 1 ("complete by deadline") is not
    encoded here: every candidate reaching this function already passed
    verification, which already requires completing by the deadline, so
    it can never differentiate between already-valid candidates -- it is
    a gate applied earlier (during verification), not a ranking tie-break.
    """
    has_spending_changes = 1 if plan.spending_changes else 0  # #2: no spending changes wins (always 0 here)
    total_paid = plan.total_payable                            # #3: minimize total amount paid
    start_date = plan.payment_dates[0]                          # #4: start earlier
    number_of_payments = plan.number_of_payments                # #5: fewer payments
    option_rank = _payment_option_sort_key(plan.source_payment_option_id)  # #6: lowest payment_option_id
    return (has_spending_changes, total_paid, start_date, number_of_payments, option_rank)


def _rank_candidates(plans: Sequence[PaymentPlan]) -> List[PaymentPlan]:
    return sorted(plans, key=_rank_key)


def _select_best(
    request: Request, valid_candidates: Sequence[VerifiedCandidate]
) -> Tuple[str, str, Optional[PaymentPlan]]:
    """Resolve the affordability_status hierarchy FIRST (Part 11), then
    rank within whichever tier actually has a valid candidate (Part 13).
    Never compares candidates from different tiers against each other."""
    by_method: Dict[str, List[PaymentPlan]] = {}
    for vc in valid_candidates:
        by_method.setdefault(vc.plan.method, []).append(vc.plan)

    # Tier 1 -- AFFORDABLE_NOW: full payment safe exactly on request_date
    # with NO spending changes. A full_payment candidate that only works
    # *with* a spending change belongs in tier 2 instead (Phase 5 Part 20;
    # this is exactly what makes sample request_06 affordable_with_plan,
    # not affordable_now).
    now_candidates = [p for p in by_method.get("full_payment", []) if not p.spending_changes]
    if now_candidates:
        return "affordable_now", "full_payment", now_candidates[0]

    # Tier 2 -- AFFORDABLE_WITH_PLAN: partial payment, a supplied
    # installment option, or a full_payment made possible by a permitted
    # spending change.
    plan_candidates = (
        list(by_method.get("partial_payment", []))
        + list(by_method.get("installments", []))
        + [p for p in by_method.get("full_payment", []) if p.spending_changes]
    )
    if plan_candidates:
        best = _rank_candidates(plan_candidates)[0]
        return "affordable_with_plan", best.method, best

    # Tier 3 -- AFFORDABLE_LATER: nothing works today, but a confirmed
    # future date (<= deadline) makes full payment safe.
    wait_candidates = by_method.get("wait", [])
    if wait_candidates:
        best = _rank_candidates(wait_candidates)[0]
        return "affordable_later", "wait", best

    # Tier 4 -- NOT_AFFORDABLE: nothing safe and eligible exists.
    return "not_affordable", "not_recommended", None


def make_decision(
    request: Request,
    state: FinancialState,
    options: Sequence[PaymentOption],
    horizon_days: int = forecast.DEFAULT_HORIZON_DAYS,
) -> DecisionResult:
    """The Phase 4 entry point: generate candidates, verify every one
    independently, then pick the best per the status hierarchy + ranking.

    `amount_safe_to_pay` and `earliest_date_for_full_payment` are computed
    directly from Phase 3 here, NOT derived from whichever plan ends up
    recommended -- per Part 16, they must stay independent of payment
    method, installment options, spending changes, and this function's
    own ranking.
    """
    if request.requested_amount < 0:
        raise ValueError(f"{request.request_id}: requested_amount must not be negative")

    amount_safe_to_pay = forecast.maximum_safe_payment(state, request.request_date, request.request_date)
    amount_safe_to_pay = min(amount_safe_to_pay, request.requested_amount)
    amount_safe_to_pay = max(amount_safe_to_pay, Decimal(0))

    earliest_date_for_full_payment = forecast.earliest_safe_payment_date(
        state, request.request_date, request.requested_amount, deadline=None, horizon_days=horizon_days,
    )

    options_by_id = {o.payment_option_id: o for o in options}
    raw_candidates = payment_plans.generate_candidate_plans(request, state, options)
    verified = [
        VerifiedCandidate(plan, verifier.verify_plan(plan, request, state, options_by_id, horizon_days))
        for plan in raw_candidates
    ]

    # Spending changes are only ever explored when they might actually
    # matter (Phase 5 Part 19: "do not change spending unless necessary")
    # -- specifically, when no zero-change full_payment candidate is
    # already valid today. This can never demote a plan that already
    # earns tier 1 without any change.
    already_affordable_now = any(
        vc.verification.valid and vc.plan.method == "full_payment" and not vc.plan.spending_changes
        for vc in verified
    )
    if not already_affordable_now:
        for plan in spending_changes.generate_spending_change_candidates(request, state):
            verified.append(VerifiedCandidate(plan, verifier.verify_plan(plan, request, state, options_by_id, horizon_days)))

    valid_candidates = [vc for vc in verified if vc.verification.valid]

    status, method, chosen_plan = _select_best(request, valid_candidates)
    spending_changes_needed = chosen_plan.spending_changes if chosen_plan is not None else ()

    return DecisionResult(
        request_id=request.request_id,
        amount_safe_to_pay=amount_safe_to_pay,
        affordability_status=status,
        recommended_payment_method=method,
        payment_plan=chosen_plan,
        earliest_date_for_full_payment=earliest_date_for_full_payment,
        spending_changes_needed=spending_changes_needed,
        candidates=tuple(verified),
    )
