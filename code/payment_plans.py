"""Candidate payment-plan generation (Phase 4).

Turns one request + its FinancialState + its supplied payment options into
a list of *candidate* `PaymentPlan` objects -- full payment, partial
payment, one per eligible supplied installment option, and wait. This
module only proposes structurally well-formed candidates using Phase 3's
deterministic safety primitives to size/date them; it never claims a
candidate is safe on its own authority. `verifier.py` independently
re-derives and checks everything before any candidate is trusted -- see
its module docstring for why.

Explicitly out of scope here: spending-change optimization (every
generated plan's `spending_changes` is always empty), messages/images,
and the final affordability_status/ranking decision (`decision_engine.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

import forecast
from data_loader import PaymentOption, Request
from financial_state import FinancialState


@dataclass(frozen=True)
class PaymentPlan:
    """A structurally complete candidate plan. Nothing here is a safety
    claim -- `verifier.verify_plan` independently checks every field."""

    method: str  # "full_payment" | "partial_payment" | "installments" | "wait"
    payment_dates: Tuple[date, ...]
    payment_amounts: Tuple[Decimal, ...]  # parallel to payment_dates, all positive
    number_of_payments: int
    total_payable: Decimal  # sum(payment_amounts); for installments this bakes in financing_fee, matching request_payment_options.csv's own convention
    financing_fee: Decimal
    full_payment_date: date  # the date the ENTIRE requested_amount is completed by
    source_payment_option_id: Optional[str] = None
    #: Always empty in this phase -- spending-change optimization is Phase 5's job.
    spending_changes: Tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# A. Full payment
# ---------------------------------------------------------------------------


def generate_full_payment_plan(request: Request, state: FinancialState) -> Optional[PaymentPlan]:
    """A full-payment candidate: `requested_amount` paid on `request_date`,
    exactly as Part 4 defines it. Structural only -- whether it is
    actually *safe* is for the verifier to determine; this only checks
    the one eligibility gate generation itself is responsible for (does
    the user accept the method at all)."""
    if "full_payment" not in state.payment_methods_user_will_consider:
        return None
    return PaymentPlan(
        method="full_payment",
        payment_dates=(request.request_date,),
        payment_amounts=(request.requested_amount,),
        number_of_payments=1,
        total_payable=request.requested_amount,
        financing_fee=Decimal(0),
        full_payment_date=request.request_date,
    )


# ---------------------------------------------------------------------------
# B. Partial payment
# ---------------------------------------------------------------------------


def generate_partial_payment_plan(request: Request, state: FinancialState) -> Optional[PaymentPlan]:
    """A two-payment candidate: the maximum amount safely payable today,
    then the exact remainder on the earliest date it can safely be paid
    -- WITH the first payment already committed (Part 20: the two legs
    are never sized against independent, forgetful forecasts).
    """
    if not request.allows_partial_payment:
        return None
    if "partial_payment" not in state.payment_methods_user_will_consider:
        return None

    safe_amount = forecast.maximum_safe_payment(state, request.request_date, request.request_date)
    safe_amount = min(safe_amount, request.requested_amount)
    if not (Decimal(0) < safe_amount < request.requested_amount):
        return None

    remaining = request.requested_amount - safe_amount  # exact Decimal subtraction -- the two legs always sum exactly
    remainder_date = forecast.earliest_safe_payment_date(
        state, request.request_date, remaining,
        deadline=request.desired_completion_date,
        prior_payments=[(request.request_date, safe_amount)],
    )
    if remainder_date is None:
        return None

    return PaymentPlan(
        method="partial_payment",
        payment_dates=(request.request_date, remainder_date),
        payment_amounts=(safe_amount, remaining),
        number_of_payments=2,
        total_payable=safe_amount + remaining,
        financing_fee=Decimal(0),
        full_payment_date=remainder_date,
    )


# ---------------------------------------------------------------------------
# C. Installments
# ---------------------------------------------------------------------------


def reconstruct_installment_dates(option: PaymentOption) -> Tuple[date, ...]:
    """Reproduce a supplied option's exact payment dates from
    `first_payment_date` + `payment_frequency_days` * i, per
    `payment_frequency_days`'s own definition in problem_statement.md
    ("the number of days between recurring payments"). Never invents a
    schedule (Part 7/8) -- a single-payment option (frequency blank)
    simply has one date."""
    if option.number_of_payments <= 1 or option.payment_frequency_days is None:
        return (option.first_payment_date,)
    return tuple(
        option.first_payment_date + timedelta(days=option.payment_frequency_days * i)
        for i in range(option.number_of_payments)
    )


def installment_option_months(option: PaymentOption) -> Optional[int]:
    """The "number of months" an installment option spans, for comparison
    against `max_installment_months`. Every installment option actually
    observed in this dataset (Phase 0) uses a monthly-ish cadence
    (`payment_frequency_days` in {28, 30, 31}), so `number_of_payments`
    directly is "months" in that case. Any other cadence can't be
    confidently mapped to "months" without inventing semantics the
    dataset doesn't support, so it returns None (the option is then
    treated as not comparable to max_installment_months, i.e. excluded)
    rather than guessed at.
    """
    if option.payment_frequency_days is None:
        return None
    if 27 <= option.payment_frequency_days <= 31:
        return option.number_of_payments
    return None


def generate_installment_plans(
    request: Request, state: FinancialState, options: Sequence[PaymentOption]
) -> List[PaymentPlan]:
    """One candidate per eligible supplied installment option -- never an
    invented schedule (Part 7). Eligibility (Part 15): the user accepts
    installments, `max_installment_months` is set, and the option's own
    span does not exceed it.
    """
    if "installments" not in state.payment_methods_user_will_consider:
        return []
    if state.max_installment_months is None:
        return []

    plans: List[PaymentPlan] = []
    for option in sorted(options, key=lambda o: o.payment_option_id):
        if option.payment_method != "installments" or option.request_id != request.request_id:
            continue
        months = installment_option_months(option)
        if months is None or months > state.max_installment_months:
            continue
        dates = reconstruct_installment_dates(option)
        amounts = tuple(option.payment_amount for _ in range(option.number_of_payments))
        plans.append(PaymentPlan(
            method="installments",
            payment_dates=dates,
            payment_amounts=amounts,
            number_of_payments=option.number_of_payments,
            total_payable=option.total_payable_amount,
            financing_fee=option.financing_fee,
            full_payment_date=dates[-1],
            source_payment_option_id=option.payment_option_id,
        ))
    return plans


# ---------------------------------------------------------------------------
# D. Wait
# ---------------------------------------------------------------------------


def generate_wait_plan(request: Request, state: FinancialState) -> Optional[PaymentPlan]:
    """A one-payment candidate: the full requested_amount, on the
    earliest date the RAW baseline forecast says it becomes safe (Part
    9) -- never using spending changes or installment options to find
    that date. If the earliest safe date is `request_date` itself, that
    is `affordable_now` territory, not "waiting"; no wait candidate is
    generated for it (the full-payment candidate already covers it)."""
    if "full_payment" not in state.payment_methods_user_will_consider:
        return None
    earliest = forecast.earliest_safe_payment_date(
        state, request.request_date, request.requested_amount, deadline=request.desired_completion_date)
    if earliest is None or earliest == request.request_date:
        return None
    return PaymentPlan(
        method="wait",
        payment_dates=(earliest,),
        payment_amounts=(request.requested_amount,),
        number_of_payments=1,
        total_payable=request.requested_amount,
        financing_fee=Decimal(0),
        full_payment_date=earliest,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def generate_candidate_plans(
    request: Request, state: FinancialState, options: Sequence[PaymentOption]
) -> List[PaymentPlan]:
    """Every structurally eligible candidate (A-D from Part 22). Plans the
    user cannot accept, or that the request does not support, are never
    generated at all -- not generated-then-rejected."""
    if request.requested_amount < 0:
        raise ValueError(f"{request.request_id}: requested_amount must not be negative")

    candidates: List[PaymentPlan] = []
    full = generate_full_payment_plan(request, state)
    if full is not None:
        candidates.append(full)
    partial = generate_partial_payment_plan(request, state)
    if partial is not None:
        candidates.append(partial)
    candidates.extend(generate_installment_plans(request, state, options))
    wait = generate_wait_plan(request, state)
    if wait is not None:
        candidates.append(wait)
    return candidates
