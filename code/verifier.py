"""Independent payment-plan verification (Phase 4).

"Do not trust generated plans." (implementation/phase4.md Part 17) --
`payment_plans.py` proposes candidates using Phase 3's safety primitives,
but this module re-derives and re-checks every fact from scratch before a
candidate is allowed to influence a decision:

* every structural field (dates ordered, within [request_date,
  desired_completion_date], positive amounts, sums that add up exactly)
* every method-specific rule (partial payment's two legs, an installment's
  exact match to its supplied option, full payment's single date/amount,
  wait's single future date)
* whole-plan safety, simulated as ONE combined scenario via
  `forecast.simulate_payments` (Part 19) -- two payments that are each
  individually safe can still combine to breach the minimum balance, so
  no candidate is ever checked payment-by-payment.

Never repairs an invalid candidate -- only reports why it failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Tuple

import forecast
from data_loader import PaymentOption, Request
from financial_state import FinancialState
from payment_plans import PaymentPlan, installment_option_months, reconstruct_installment_dates
from spending_changes import MAX_SPENDING_CHANGES, SpendingChange, apply_spending_changes, validate_spending_changes

DEFAULT_HORIZON_DAYS = forecast.DEFAULT_HORIZON_DAYS

_SUPPORTED_METHODS = frozenset({"full_payment", "partial_payment", "installments", "wait"})


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    errors: Tuple[str, ...]
    warnings: Tuple[str, ...] = ()


def _check_structural_rules(plan: PaymentPlan, request: Request, errors: List[str]) -> None:
    # 1: dates present
    if any(d is None for d in plan.payment_dates):
        errors.append("a payment date is missing")
        return  # nothing further can be safely checked

    # 2: dates ordered
    if list(plan.payment_dates) != sorted(plan.payment_dates):
        errors.append(f"payment dates {plan.payment_dates} are not in chronological order")

    # 3-4: date bounds
    for d in plan.payment_dates:
        if d < request.request_date:
            errors.append(f"payment on {d} occurs before request_date {request.request_date}")
        if d > request.desired_completion_date:
            errors.append(f"payment on {d} occurs after desired_completion_date {request.desired_completion_date}")

    # 5: positive amounts "where required" (Part 17) -- a zero-amount
    # payment is only legitimate for the degenerate requested_amount == 0
    # case (Part 25); any negative amount, or a zero amount on a request
    # that actually costs something, is always rejected.
    if any(a < 0 for a in plan.payment_amounts):
        errors.append("no payment amount may be negative")
    elif request.requested_amount != 0 and any(a == 0 for a in plan.payment_amounts):
        errors.append("every payment amount must be positive for a nonzero request")

    # 6/7: totals are internally consistent
    if plan.total_payable != sum(plan.payment_amounts):
        errors.append(
            f"total_payable {plan.total_payable} does not equal the sum of payment_amounts "
            f"{sum(plan.payment_amounts)}"
        )

    # 8: supported method
    if plan.method not in _SUPPORTED_METHODS:
        errors.append(f"unsupported payment method {plan.method!r}")

    # 13/33: at most MAX_SPENDING_CHANGES, each syntactically one of the
    # two allowed shapes (Part 13). Semantic eligibility (protected
    # category, flexibility, minimum_allowed_amount, ...) is re-checked
    # in verify_plan itself, against the FinancialState, independently
    # of whatever the generator believed.
    if len(plan.spending_changes) > MAX_SPENDING_CHANGES:
        errors.append(f"at most {MAX_SPENDING_CHANGES} spending changes are allowed, got {len(plan.spending_changes)}")
    for raw in plan.spending_changes:
        if SpendingChange.parse(raw) is None:
            errors.append(f"spending change {raw!r} is not a valid stop:/reduce_to: operation")

    # 21: plan must complete by the deadline
    if plan.full_payment_date > request.desired_completion_date:
        errors.append(
            f"plan completes on {plan.full_payment_date}, after desired_completion_date "
            f"{request.desired_completion_date}"
        )


def _check_user_accepts_method(plan: PaymentPlan, state: FinancialState, errors: List[str]) -> None:
    # 10: wait is gated on full_payment acceptance (Part 9/14) -- "wait" is
    # never itself a value inside payment_methods_user_will_consider.
    if plan.method == "wait":
        if "full_payment" not in state.payment_methods_user_will_consider:
            errors.append("user does not accept full_payment, so wait (an eventual full payment) is not eligible")
    elif plan.method not in state.payment_methods_user_will_consider:
        errors.append(f"user does not accept {plan.method!r}")


def _check_full_payment(plan: PaymentPlan, request: Request, errors: List[str]) -> None:
    # 17-18
    if len(plan.payment_dates) != 1:
        errors.append("full_payment must have exactly one payment")
        return
    if plan.payment_dates[0] != request.request_date:
        errors.append(f"full_payment must be on request_date {request.request_date}, got {plan.payment_dates[0]}")
    if plan.payment_amounts[0] != request.requested_amount:
        errors.append(
            f"full_payment amount {plan.payment_amounts[0]} must equal requested_amount {request.requested_amount}"
        )


def _check_partial_payment(plan: PaymentPlan, request: Request, state: FinancialState, errors: List[str]) -> None:
    if not request.allows_partial_payment:
        errors.append("request does not allow partial payment")
    # 14
    if len(plan.payment_dates) != 2:
        errors.append("partial_payment must have exactly two payments")
        return

    # 15: first payment independently re-derived, not merely trusted
    expected_first = forecast.maximum_safe_payment(state, request.request_date, request.request_date)
    expected_first = min(expected_first, request.requested_amount)
    if plan.payment_amounts[0] != expected_first:
        errors.append(
            f"partial_payment first payment {plan.payment_amounts[0]} does not equal the independently "
            f"recomputed safe amount {expected_first}"
        )
    if not (Decimal(0) < plan.payment_amounts[0] < request.requested_amount):
        errors.append("partial_payment first payment must be strictly between 0 and requested_amount")

    # 16
    expected_second = request.requested_amount - plan.payment_amounts[0]
    if plan.payment_amounts[1] != expected_second:
        errors.append(
            f"partial_payment second payment {plan.payment_amounts[1]} does not equal the remaining amount "
            f"{expected_second}"
        )
    if plan.payment_amounts[0] + plan.payment_amounts[1] != request.requested_amount:
        errors.append("partial_payment's two payments do not sum exactly to requested_amount")


def _check_installments(
    plan: PaymentPlan, request: Request, state: FinancialState,
    options_by_id: Dict[str, PaymentOption], errors: List[str],
) -> None:
    if plan.source_payment_option_id is None:
        errors.append("installments plan has no source_payment_option_id")
        return
    option = options_by_id.get(plan.source_payment_option_id)
    if option is None:
        errors.append(
            f"payment_option_id {plan.source_payment_option_id!r} is not one of this request's supplied options"
        )
        return
    if option.request_id != request.request_id:
        errors.append(f"payment_option_id {option.payment_option_id!r} belongs to a different request")
    if option.payment_method != "installments":
        errors.append(f"payment_option_id {option.payment_option_id!r} is not an installments option")

    # Part 18: EXACT match against the supplied option, field by field, Decimal comparisons only.
    if plan.number_of_payments != option.number_of_payments:
        errors.append(f"number_of_payments {plan.number_of_payments} != option's {option.number_of_payments}")
    if plan.financing_fee != option.financing_fee:
        errors.append(f"financing_fee {plan.financing_fee} != option's {option.financing_fee}")
    if plan.total_payable != option.total_payable_amount:
        errors.append(f"total_payable {plan.total_payable} != option's total_payable_amount {option.total_payable_amount}")
    if any(a != option.payment_amount for a in plan.payment_amounts):
        errors.append(f"payment amounts {plan.payment_amounts} do not all equal the option's payment_amount {option.payment_amount}")
    expected_dates = reconstruct_installment_dates(option)
    if plan.payment_dates != expected_dates:
        errors.append(f"payment_dates {plan.payment_dates} do not match the option's schedule {expected_dates}")

    # Part 15, re-checked independently rather than trusting generation.
    months = installment_option_months(option)
    if state.max_installment_months is None:
        errors.append("user's max_installment_months is None -- installments are not eligible at all")
    elif months is None or months > state.max_installment_months:
        errors.append(
            f"installment option spans {months} months, exceeding max_installment_months "
            f"{state.max_installment_months}"
        )


def _check_wait(plan: PaymentPlan, request: Request, errors: List[str]) -> None:
    # 19
    if len(plan.payment_dates) != 1:
        errors.append("wait must have exactly one payment")
        return
    if plan.payment_dates[0] <= request.request_date:
        errors.append("wait must pay strictly after request_date (otherwise it is a full_payment, not a wait)")
    if plan.payment_amounts[0] != request.requested_amount:
        errors.append(
            f"wait amount {plan.payment_amounts[0]} must equal requested_amount {request.requested_amount}"
        )


def verify_plan(
    plan: PaymentPlan,
    request: Request,
    state: FinancialState,
    options_by_id: Dict[str, PaymentOption],
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> VerificationResult:
    """Independently verify one candidate plan. Returns a fresh
    VerificationResult built entirely from re-derived facts -- never from
    reading a "valid" flag the generator might have set."""
    errors: List[str] = []
    warnings: List[str] = []

    _check_structural_rules(plan, request, errors)
    _check_user_accepts_method(plan, state, errors)

    if plan.method == "full_payment":
        _check_full_payment(plan, request, errors)
    elif plan.method == "partial_payment":
        _check_partial_payment(plan, request, state, errors)
    elif plan.method == "installments":
        _check_installments(plan, request, state, options_by_id, errors)
    elif plan.method == "wait":
        _check_wait(plan, request, errors)
    # An unsupported method was already flagged by _check_structural_rules;
    # there is nothing more method-specific to check for it.

    # Spending changes (Phase 5, Part 14): re-validate every eligibility
    # rule from scratch against the FinancialState -- never trust that the
    # generator already checked them. Simulate the plan against the
    # resulting ADJUSTED state, never the original, so the safety check
    # actually reflects what the plan proposes to change.
    simulation_state = state
    parsed_changes = [c for c in (SpendingChange.parse(raw) for raw in plan.spending_changes) if c is not None]
    if plan.spending_changes:
        changes_valid, change_errors = validate_spending_changes(state, parsed_changes)
        errors.extend(change_errors)
        if changes_valid:
            simulation_state = apply_spending_changes(state, parsed_changes)

    # 11-12, 19 (Part 19 numbering): simulate the WHOLE plan as one
    # scenario -- never payment-by-payment, since two individually-safe
    # payments can still combine to breach the minimum balance.
    if plan.payment_dates and plan.payment_amounts and len(plan.payment_dates) == len(plan.payment_amounts):
        try:
            result = forecast.simulate_payments(
                simulation_state, request.request_date, list(zip(plan.payment_dates, plan.payment_amounts)), horizon_days,
            )
        except ValueError as exc:
            errors.append(f"could not simulate the plan: {exc}")
        else:
            if not result.is_safe:
                errors.append(
                    f"plan is not safe: projected balance falls to {result.minimum_projected_balance} on "
                    f"{result.minimum_projected_balance_date} (minimum required: {simulation_state.minimum_balance_to_keep})"
                )
            if result.has_unresolved_evidence:
                warnings.append(
                    f"forecast has unresolved blank-amount evidence within the horizon: "
                    f"{result.unresolved_relevant_event_ids}"
                )

    return VerificationResult(valid=not errors, errors=tuple(errors), warnings=tuple(warnings))
