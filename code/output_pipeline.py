"""Final production pipeline (Phase 6).

The ONE authoritative path from a `Request` to a serialized `output.csv`
row: `DataStore` -> `FinancialState` -> `EvidenceBundle` -> evidence-aware
state -> `decision_engine.make_decision` (which internally runs the
forecast, candidate generation, spending-change search, and
verification) -> a formatted `OutputRow`. Every earlier phase's module is
reused exactly as built (Part 1: "do not create special-case logic for
individual request IDs", "do not hardcode sample answers"); this module
adds only CSV serialization, a deterministic explanation template, and an
independent, from-scratch validation pass over the *final* row (Part 8:
"do not trust only the stored VerificationResult -- re-run the actual
relevant calculations").

No LLM or live model call is used anywhere in this module -- explanations
are built from a fixed, deterministic string template over the decision's
own already-verified fields, never free-form generation (Part 7: "do not
allow an LLM to invent explanations that contradict the actual plan").
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import evidence
import forecast
import spending_changes
from data_loader import DataStore, PaymentOption, Request
from currency import CurrencyConverter
from decision_engine import DecisionResult, make_decision
from evidence import EvidenceResolution
from financial_state import FinancialState, build_financial_state
from payment_plans import PaymentPlan, installment_option_months, reconstruct_installment_dates
from spending_changes import SpendingChange

OUTPUT_COLUMNS: Tuple[str, ...] = (
    "request_id", "amount_safe_to_pay", "affordability_status",
    "recommended_payment_method", "payment_plan",
    "earliest_date_for_full_payment", "spending_changes_needed",
    "decision_explanation",
)

_VALID_STATUSES = frozenset({"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"})
_VALID_METHODS = frozenset({"full_payment", "partial_payment", "installments", "wait", "not_recommended"})
#: The only method(s) a given status may pair with (Part 9).
_STATUS_METHOD_MAP: Dict[str, frozenset] = {
    "affordable_now": frozenset({"full_payment"}),
    "affordable_with_plan": frozenset({"full_payment", "partial_payment", "installments"}),
    "affordable_later": frozenset({"wait"}),
    "not_affordable": frozenset({"not_recommended"}),
}

VALIDATION_CATEGORIES: Tuple[str, ...] = (
    "schema", "numeric", "payment_plan", "deadline", "unsafe_plan", "spending_change", "other",
)


# ---------------------------------------------------------------------------
# Serialization (Part 4/5/12)
# ---------------------------------------------------------------------------


def format_money(value: Decimal) -> str:
    """Fixed-point string for a Decimal money value -- never scientific
    notation, never rounded beyond what the value already carries
    (Part 12)."""
    return format(value, "f")


def format_date(d: Optional[date]) -> str:
    return d.isoformat() if d is not None else ""


def format_payment_plan(plan: Optional[PaymentPlan]) -> str:
    """`<YYYY-MM-DD>:<amount>` legs joined by `|`, chronological, or the
    literal `none` when no payment is recommended (problem_statement.md's
    "Allowed values" section, not phase6.md's own shorthand notation)."""
    if plan is None or not plan.payment_dates:
        return "none"
    return "|".join(f"{d.isoformat()}:{format_money(a)}" for d, a in zip(plan.payment_dates, plan.payment_amounts))


def format_spending_changes(changes: Sequence[str]) -> str:
    if not changes:
        return "none"
    return "|".join(changes)


def _parse_plan_string(text: str) -> List[Tuple[date, Decimal]]:
    if text == "none":
        return []
    legs = []
    for leg in text.split("|"):
        d_str, a_str = leg.split(":")
        legs.append((date.fromisoformat(d_str), Decimal(a_str)))
    return legs


def _parse_spending_changes_string(text: str) -> List[str]:
    if text == "none":
        return []
    return text.split("|")


# ---------------------------------------------------------------------------
# Deterministic explanation (Part 7)
# ---------------------------------------------------------------------------


def build_decision_explanation(
    request: Request, state: FinancialState, decision: DecisionResult,
    material_resolutions: Sequence[EvidenceResolution],
) -> str:
    """A concise, template-based explanation grounded entirely in the
    decision's own already-verified fields. Never claims a payment was
    made, an external account was checked, live data was consulted, or
    that a spending change/evidence resolution occurred when it did not
    (Part 7)."""
    currency = state.home_currency
    plan = decision.payment_plan
    parts: List[str] = [
        f"Requested {format_money(request.requested_amount)} {currency}; "
        f"up to {format_money(decision.amount_safe_to_pay)} {currency} is safe to pay on "
        f"{request.request_date} while keeping the {format_money(state.minimum_balance_to_keep)} "
        f"{currency} minimum balance."
    ]

    if decision.affordability_status == "affordable_now":
        parts.append(f"The full amount is safe today, so full payment on {request.request_date} is recommended.")
    elif decision.affordability_status == "affordable_with_plan" and plan is not None:
        if decision.recommended_payment_method == "partial_payment":
            parts.append(
                f"Full payment today is not safe, so a partial-payment plan is recommended: "
                f"{format_money(plan.payment_amounts[0])} {currency} on {request.request_date}, then "
                f"{format_money(plan.payment_amounts[1])} {currency} on {plan.payment_dates[1]}."
            )
        elif decision.recommended_payment_method == "installments":
            parts.append(
                f"Full payment today is not safe, so the supplied installment option "
                f"{plan.source_payment_option_id} is recommended: {plan.number_of_payments} payment(s) of "
                f"{format_money(plan.payment_amounts[0])} {currency} starting {plan.payment_dates[0]}."
            )
        elif decision.recommended_payment_method == "full_payment":
            changes = ", ".join(decision.spending_changes_needed)
            parts.append(
                f"Full payment on {request.request_date} becomes safe once this spending change is applied: "
                f"{changes}."
            )
    elif decision.affordability_status == "affordable_later" and plan is not None:
        parts.append(
            f"Full payment is not safe today; it is projected to become safe on {plan.payment_dates[0]}, "
            f"so waiting is recommended."
        )
    else:
        parts.append(
            "No payment method the user accepts can safely complete this request within the 90-day "
            "forecast, even with the maximum permitted spending changes, so no payment is recommended."
        )

    if material_resolutions:
        parts.append(
            f"This recommendation reflects {len(material_resolutions)} evidence update(s) from "
            f"statements, messages, or images that changed the outcome."
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Output row
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutputRow:
    request_id: str
    amount_safe_to_pay: Decimal
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: str
    spending_changes_needed: str
    decision_explanation: str

    def as_csv_row(self) -> List[str]:
        return [
            self.request_id,
            format_money(self.amount_safe_to_pay),
            self.affordability_status,
            self.recommended_payment_method,
            self.payment_plan,
            self.earliest_date_for_full_payment,
            self.spending_changes_needed,
            self.decision_explanation,
        ]


def build_output_row(
    request: Request, state: FinancialState, decision: DecisionResult,
    material_resolutions: Sequence[EvidenceResolution],
) -> OutputRow:
    return OutputRow(
        request_id=request.request_id,
        amount_safe_to_pay=decision.amount_safe_to_pay,
        affordability_status=decision.affordability_status,
        recommended_payment_method=decision.recommended_payment_method,
        payment_plan=format_payment_plan(decision.payment_plan),
        earliest_date_for_full_payment=format_date(decision.earliest_date_for_full_payment),
        spending_changes_needed=format_spending_changes(decision.spending_changes_needed),
        decision_explanation=build_decision_explanation(request, state, decision, material_resolutions),
    )


# ---------------------------------------------------------------------------
# Independent, from-scratch row validation (Part 8/11)
# ---------------------------------------------------------------------------


def validate_output_row(
    row: OutputRow,
    request: Request,
    state: FinancialState,
    decision: DecisionResult,
    options_by_id: Dict[str, PaymentOption],
    horizon_days: int = forecast.DEFAULT_HORIZON_DAYS,
) -> List[Tuple[str, str]]:
    """Re-derive and re-check every Part 8 rule against the FINAL,
    already-serialized row -- re-parsing the actual CSV strings (never
    just the in-memory objects that produced them) and re-running the
    actual safety simulation fresh, never reusing a stored
    VerificationResult. Returns a list of `(category, message)` issues;
    empty means the row passed every check."""
    issues: List[Tuple[str, str]] = []

    def add(category: str, message: str) -> None:
        issues.append((category, message))

    # 1: request_id
    if row.request_id != request.request_id:
        add("schema", "request_id does not match the request being validated")

    # 2-4: amount bounds
    if not isinstance(row.amount_safe_to_pay, Decimal):
        add("numeric", "amount_safe_to_pay is not a Decimal")
    else:
        if row.amount_safe_to_pay < 0:
            add("numeric", "amount_safe_to_pay is negative")
        if row.amount_safe_to_pay > request.requested_amount:
            add("numeric", "amount_safe_to_pay exceeds requested_amount")

    # 5-7: allowed values and status/method consistency
    if row.affordability_status not in _VALID_STATUSES:
        add("schema", f"affordability_status {row.affordability_status!r} is not an allowed value")
    if row.recommended_payment_method not in _VALID_METHODS:
        add("schema", f"recommended_payment_method {row.recommended_payment_method!r} is not an allowed value")
    if (row.affordability_status in _STATUS_METHOD_MAP
            and row.recommended_payment_method not in _STATUS_METHOD_MAP[row.affordability_status]):
        add("schema", f"status {row.affordability_status!r} is inconsistent with method {row.recommended_payment_method!r}")

    # Round-trip the actual CSV strings (catches serialization bugs the
    # in-memory objects alone could never reveal).
    parsed_plan: Optional[List[Tuple[date, Decimal]]]
    try:
        parsed_plan = _parse_plan_string(row.payment_plan)
    except Exception as exc:  # noqa: BLE001 -- any parse failure is itself the finding
        add("payment_plan", f"payment_plan string {row.payment_plan!r} is not parseable: {exc}")
        parsed_plan = None

    try:
        parsed_changes_raw = _parse_spending_changes_string(row.spending_changes_needed)
    except Exception as exc:  # noqa: BLE001
        add("spending_change", f"spending_changes_needed string {row.spending_changes_needed!r} is not parseable: {exc}")
        parsed_changes_raw = []

    plan = decision.payment_plan

    # 24: contradictory fields
    if row.recommended_payment_method == "not_recommended":
        if row.payment_plan != "none":
            add("schema", "not_recommended must have payment_plan == 'none'")
        if plan is not None:
            add("schema", "not_recommended must not carry an internal payment_plan object")
    elif plan is None:
        add("schema", f"{row.recommended_payment_method} is missing its internal payment_plan object")

    if row.affordability_status == "affordable_now" and row.earliest_date_for_full_payment != request.request_date.isoformat():
        add("schema", "affordable_now must have earliest_date_for_full_payment == request_date")
    if row.affordability_status == "affordable_later" and not row.earliest_date_for_full_payment:
        add("schema", "affordable_later must have a non-empty earliest_date_for_full_payment")

    parsed_changes = [SpendingChange.parse(s) for s in parsed_changes_raw]
    changes_syntactically_valid = all(c is not None for c in parsed_changes)
    if not changes_syntactically_valid:
        add("spending_change", "spending_changes_needed contains a syntactically invalid entry")

    if plan is not None and parsed_plan is not None:
        # 8: round-trip consistency
        expected_legs = list(zip(plan.payment_dates, plan.payment_amounts))
        if parsed_plan != expected_legs:
            add("payment_plan", "payment_plan string does not round-trip to the decision's own dates/amounts")

        # 9: sums
        total = sum(a for _, a in parsed_plan) if parsed_plan else Decimal(0)
        if plan.method in ("full_payment", "partial_payment", "wait"):
            if total != request.requested_amount:
                add("numeric", f"payment amounts sum to {total}, not requested_amount {request.requested_amount}")
        elif plan.method == "installments":
            option = options_by_id.get(plan.source_payment_option_id)
            if option is not None and total != option.total_payable_amount:
                add("numeric", f"installment amounts sum to {total}, not the option's total_payable_amount {option.total_payable_amount}")

        # 10-11: date ordering and deadline
        dates_only = [d for d, _ in parsed_plan]
        if dates_only != sorted(dates_only):
            add("payment_plan", "payment_plan dates are not in chronological order")
        for d in dates_only:
            if d < request.request_date:
                add("payment_plan", f"payment on {d} occurs before request_date")
            if d > request.desired_completion_date:
                add("deadline", f"payment on {d} occurs after desired_completion_date {request.desired_completion_date}")

        # 12-13: partial payment
        if plan.method == "partial_payment":
            if len(parsed_plan) != 2:
                add("payment_plan", "partial_payment must have exactly two payments")
            else:
                expected_first = min(
                    forecast.maximum_safe_payment(state, request.request_date, request.request_date),
                    request.requested_amount,
                )
                if parsed_plan[0][1] != expected_first:
                    add("payment_plan", "partial_payment's first leg does not match the independently recomputed safe amount")
                if not (Decimal(0) < parsed_plan[0][1] < request.requested_amount):
                    add("payment_plan", "partial_payment's first leg must be strictly between 0 and requested_amount")
                if parsed_plan[0][1] + parsed_plan[1][1] != request.requested_amount:
                    add("payment_plan", "partial_payment's two legs do not sum exactly to requested_amount")

        # 14-15: installments
        if plan.method == "installments":
            option = options_by_id.get(plan.source_payment_option_id)
            if option is None:
                add("payment_plan", f"installments references unknown payment_option_id {plan.source_payment_option_id!r}")
            else:
                expected_dates = reconstruct_installment_dates(option)
                if tuple(dates_only) != expected_dates:
                    add("payment_plan", "installment dates do not match the supplied option's schedule")
                months = installment_option_months(option)
                if state.max_installment_months is None or months is None or months > state.max_installment_months:
                    add("payment_plan", "installment plan exceeds max_installment_months or is not eligible")

        # 16-17: wait
        if plan.method == "wait":
            if plan.payment_dates[0] <= request.request_date:
                add("payment_plan", "wait must pay strictly after request_date")
            if "full_payment" not in state.payment_methods_user_will_consider:
                add("payment_plan", "wait recommended but user does not accept full_payment")
            wait_check = forecast.can_safely_pay(state, request.request_date, plan.payment_dates[0], request.requested_amount)
            if not wait_check.is_safe:
                add("unsafe_plan", "wait's target date is not actually safe under an independent re-check")

    # 18-20: spending changes
    if changes_syntactically_valid:
        if len(parsed_changes) > spending_changes.MAX_SPENDING_CHANGES:
            add("spending_change", f"more than {spending_changes.MAX_SPENDING_CHANGES} spending changes")
        if parsed_changes:
            valid, change_errors = spending_changes.validate_spending_changes(state, parsed_changes)
            if not valid:
                for msg in change_errors:
                    add("spending_change", msg)

    # 21: earliest_date_for_full_payment is baseline-derived (independent recompute)
    expected_earliest = forecast.earliest_safe_payment_date(
        state, request.request_date, request.requested_amount, deadline=None, horizon_days=horizon_days)
    expected_earliest_str = format_date(expected_earliest)
    if row.earliest_date_for_full_payment != expected_earliest_str:
        add(
            "other",
            f"earliest_date_for_full_payment {row.earliest_date_for_full_payment!r} does not match the "
            f"independently recomputed baseline {expected_earliest_str!r}",
        )

    # 22-23: final plan safety, independently re-simulated (never reusing decision.candidates' stored result)
    if plan is not None and changes_syntactically_valid:
        sim_state = state
        if plan.spending_changes:
            sim_state = spending_changes.apply_spending_changes(state, parsed_changes)
        result = forecast.simulate_payments(
            sim_state, request.request_date, list(zip(plan.payment_dates, plan.payment_amounts)), horizon_days)
        if not result.is_safe:
            add("unsafe_plan", "recommended plan is not safe under an independent re-simulation")

    return issues


# ---------------------------------------------------------------------------
# Orchestration (Part 1/10)
# ---------------------------------------------------------------------------


@dataclass
class PipelineDiagnostics:
    total_requests: int = 0
    status_counts: Dict[str, int] = field(default_factory=dict)
    method_counts: Dict[str, int] = field(default_factory=dict)
    spending_change_count: int = 0
    evidence_applied_count: int = 0
    evidence_materially_changed_count: int = 0
    unresolved_evidence_count: int = 0
    verifier_rejection_count: int = 0
    no_valid_plan_count: int = 0
    total_runtime_seconds: float = 0.0
    state_construction_seconds: float = 0.0
    evidence_processing_seconds: float = 0.0
    decision_seconds: float = 0.0
    validation_seconds: float = 0.0


def run_production_pipeline(
    store: DataStore, converter: CurrencyConverter,
) -> Tuple[List[OutputRow], PipelineDiagnostics, Dict[str, List[Tuple[str, str]]]]:
    """Run every request in `store.requests` through the full Part 1
    pipeline. Deterministic: no randomness, no current-date dependence,
    no network I/O; `store.requests` is already in stable file order."""
    diagnostics = PipelineDiagnostics()
    rows: List[OutputRow] = []
    validation_issues: Dict[str, List[Tuple[str, str]]] = {}

    t_start = time.perf_counter()
    bundle = evidence.build_evidence_bundle(store)

    for request in store.requests:
        options = store.get_payment_options(request.request_id)
        options_by_id = {o.payment_option_id: o for o in options}

        t0 = time.perf_counter()
        base_state = build_financial_state(store, converter, request.user_id)
        t1 = time.perf_counter()
        diagnostics.state_construction_seconds += t1 - t0

        adjusted_state, resolutions = evidence.apply_evidence(base_state, bundle, converter)
        t2 = time.perf_counter()
        diagnostics.evidence_processing_seconds += t2 - t1
        if resolutions:
            diagnostics.evidence_applied_count += 1

        decision = make_decision(request, adjusted_state, options)
        t3 = time.perf_counter()

        material_resolutions: List[EvidenceResolution] = []
        if resolutions:
            baseline_decision = make_decision(request, base_state, options)
            baseline_signature = (
                baseline_decision.affordability_status, baseline_decision.recommended_payment_method,
                baseline_decision.amount_safe_to_pay, baseline_decision.earliest_date_for_full_payment,
                baseline_decision.spending_changes_needed,
            )
            final_signature = (
                decision.affordability_status, decision.recommended_payment_method,
                decision.amount_safe_to_pay, decision.earliest_date_for_full_payment,
                decision.spending_changes_needed,
            )
            if baseline_signature != final_signature:
                material_resolutions = resolutions
                diagnostics.evidence_materially_changed_count += 1
        diagnostics.decision_seconds += time.perf_counter() - t3

        for vc in decision.candidates:
            if not vc.verification.valid:
                diagnostics.verifier_rejection_count += 1
            if vc.verification.warnings:
                diagnostics.unresolved_evidence_count += 1
        if decision.recommended_payment_method == "not_recommended":
            diagnostics.no_valid_plan_count += 1
        if decision.spending_changes_needed:
            diagnostics.spending_change_count += 1
        diagnostics.status_counts[decision.affordability_status] = (
            diagnostics.status_counts.get(decision.affordability_status, 0) + 1)
        diagnostics.method_counts[decision.recommended_payment_method] = (
            diagnostics.method_counts.get(decision.recommended_payment_method, 0) + 1)

        row = build_output_row(request, adjusted_state, decision, material_resolutions)
        rows.append(row)

        t4 = time.perf_counter()
        issues = validate_output_row(row, request, adjusted_state, decision, options_by_id)
        diagnostics.validation_seconds += time.perf_counter() - t4
        if issues:
            validation_issues[request.request_id] = issues

    diagnostics.total_requests = len(rows)
    diagnostics.total_runtime_seconds = time.perf_counter() - t_start
    return rows, diagnostics, validation_issues


def write_output_csv(rows: Sequence[OutputRow], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(OUTPUT_COLUMNS)
        for row in rows:
            writer.writerow(row.as_csv_row())


def read_output_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))
