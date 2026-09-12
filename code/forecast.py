"""Deterministic 90-day financial forecasting & safety simulation (Phase 3).

Turns a Phase 2 `FinancialState` into forward-looking cash-flow projections
and answers safety questions ("can this amount be paid on this date without
ever breaching the minimum balance through the forecast horizon?"). This
module makes no affordability decision, chooses no payment method, and
applies no spending change -- it only simulates and reports facts. Phase 4
consumes those facts to decide `amount_safe_to_pay`,
`affordability_status`, `recommended_payment_method`, and `payment_plan`.

Key semantics (documented once here, referenced throughout):

* **Forecast horizon**: `[start_date, start_date + horizon_days]`, default
  90 days. `start_date` is always the request's own `request_date` (never
  today's wall-clock date) -- callers pass it explicitly.
* **Starting balance**: always `financial_state.current_available_balance`,
  taken as-is from the profile. Historical `effective_events` are never
  replayed/subtracted again -- they are already reflected in that balance
  and are only useful upstream (Phase 2) for detecting recurrence.
* **Event inclusion**: known future/scheduled income and expense events,
  pending expenses (reserved as committed future obligations), and
  projected occurrences of Phase 2's recurring income/expense patterns.
  Pending *credits*, failed events, cancelled events, and non-cash events
  are never projected -- money that hasn't settled, or that never
  happened, can never make a purchase look safer.
* **Recurring vs. known-future de-duplication**: a projected recurring
  occurrence is dropped whenever a known future/scheduled/pending event of
  the same direction+category falls within a conservative date window of
  it -- see `_find_dedup_match`. When ambiguous, the synthetic occurrence
  is dropped (never double-counted), per the problem statement.
* **Same-day cash flows are netted together, once per calendar day**: the
  dataset carries no time-of-day information, and the problem statement's
  own worked example of a safety violation ("Day 10: 12,000, Day 11:
  8,000, Day 12: 15,000") checks exactly one balance value per day, not
  per transaction. So every entry dated the same day -- including a
  hypothetical/probe payment being tested -- is summed into one net
  change, and the minimum-balance check runs once, at the end of that
  day, against the post-net balance. A same-day credit (e.g. a salary
  landing on a candidate payment date) DOES fully offset a same-day debit
  under this rule (confirmed against a real solved sample,
  `sample_requests.csv`'s `request_19`, whose own answer pays the second
  partial-payment installment on the very day a salary posts and treats
  it as safe). `entries`/`balance_timeline` still record and display each
  individual line item in a stable, deterministic order for provenance --
  see `_entry_sort_key` -- but that ordering is cosmetic only and never
  affects `is_safe`/`minimum_projected_balance`/`first_violation_date`.
* **Unresolved amounts**: never coerced to zero or guessed. A blank-amount
  event that is `scheduled`/`pending` and falls inside the horizon is
  surfaced via `ForecastResult.unresolved_relevant_event_ids` /
  `has_unresolved_evidence`, without contributing any amount to the
  simulation, so a later evidence-resolution phase can plug in the real
  value without touching this engine.
* **Maximum safe payment**: derived in closed form from a suffix-minimum
  over the baseline balance timeline (see `maximum_safe_payment`) -- never
  a dollar-by-dollar brute-force search.
* **Earliest safe date**: derived by evaluating `maximum_safe_payment` only
  at the (few) dates where the baseline timeline actually changes value
  (start_date plus each distinct cash-flow date), since it is provably a
  step function between those points -- never a fine day-by-day scan of
  amounts, and bounded by the number of a single user's own cash-flow
  entries, not the full dataset.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from financial_state import FinancialState, NormalizedEvent, RecurrencePattern

DEFAULT_HORIZON_DAYS = 90


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastEntry:
    """One cash-flow line in the forecast. ``amount`` is SIGNED: positive
    for a credit (adds to balance), negative for a debit (subtracts) --
    ``direction`` is redundant with the sign but kept for readability and
    for the same-day ordering rule."""

    date: date
    amount: Decimal
    direction: str  # "credit" | "debit"
    source: str      # "known_future" | "pending_expense" | "recurring_income" |
                       # "recurring_expense" | "hypothetical_payment" | "probe"
    category: str
    description: str
    event_id: Optional[str] = None
    #: For a synthetic recurring occurrence: the Phase 2 pattern's evidence
    #: event IDs (there is no single concrete event_id for a projected
    #: future occurrence that hasn't happened yet).
    pattern_source_event_ids: Tuple[str, ...] = ()


@dataclass
class ForecastResult:
    user_id: str
    home_currency: str
    starting_balance: Decimal
    minimum_balance_to_keep: Decimal
    forecast_start_date: date
    forecast_end_date: date
    #: Every entry actually applied to the balance, in the exact
    #: deterministic order they were processed (see _entry_sort_key).
    entries: Tuple[ForecastEntry, ...]
    #: (date, balance_after) for every entry in `entries`, same order.
    balance_timeline: Tuple[Tuple[date, Decimal], ...]
    minimum_projected_balance: Decimal
    minimum_projected_balance_date: date
    is_safe: bool
    first_violation_date: Optional[date]
    violation_reason: Optional[str]
    #: Pending credits that were deliberately excluded from the simulation
    #: (Part 8/19: pending income can never rescue a purchase).
    excluded_pending_income_event_ids: Tuple[str, ...]
    #: Blank-amount events (scheduled/pending) that fall inside the
    #: forecast horizon and therefore *could* matter, but whose amount is
    #: unknown -- never zeroed, never guessed.
    unresolved_relevant_event_ids: Tuple[str, ...]
    has_unresolved_evidence: bool
    warnings: Tuple[str, ...] = ()
    #: Set only when this result came from simulate_payment(s)/can_safely_pay.
    #: A tuple because a multi-payment plan (partial payment, installments)
    #: must be simulated as one combined scenario -- see simulate_payments.
    hypothetical_entries: Tuple[ForecastEntry, ...] = ()


@dataclass(frozen=True)
class PaymentSafetyProfile:
    """The raw, method-agnostic facts Phase 4 needs (Part 13): does not
    decide affordability_status/recommended_payment_method -- just reports
    safe_now / first_safe_date / safe_by_deadline."""

    amount: Decimal
    start_date: date
    deadline: Optional[date]
    horizon_end_date: date
    safe_now: bool
    first_safe_date: Optional[date]
    safe_by_deadline: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _effective_date(n: NormalizedEvent) -> date:
    return n.settlement_date or n.event_date


def _known_future_entries(state: FinancialState, start_date: date, end_date: date) -> List[ForecastEntry]:
    entries: List[ForecastEntry] = []
    for n in state.future_income_events:
        d = _effective_date(n)
        # amount_home_currency is None only if Phase 2 could not resolve or
        # convert it -- such events are routed to unresolved_amount_events
        # instead, so this is a defensive guard, not the expected path.
        if start_date <= d <= end_date and n.amount_home_currency is not None:
            entries.append(ForecastEntry(d, n.amount_home_currency, "credit", "known_future", n.category, n.description, n.event_id))
    for n in state.future_expense_events:
        d = _effective_date(n)
        if start_date <= d <= end_date and n.amount_home_currency is not None:
            entries.append(ForecastEntry(d, -n.amount_home_currency, "debit", "known_future", n.category, n.description, n.event_id))
    return entries


def _pending_entries(state: FinancialState, start_date: date, end_date: date) -> Tuple[List[ForecastEntry], List[str]]:
    """Pending debits are reserved as committed future obligations, clamped
    to start_date if already "overdue" (their date precedes start_date,
    the conservative treatment). Pending credits are never counted --
    their event_id is returned separately so the caller can expose them as
    explicitly excluded, per Part 8/19."""
    entries: List[ForecastEntry] = []
    excluded_income_ids: List[str] = []
    for n in state.pending_events:
        if n.amount_home_currency is None:
            continue  # unresolved pending events live in unresolved_amount_events instead
        if n.direction == "credit":
            excluded_income_ids.append(n.event_id)
            continue
        d = max(_effective_date(n), start_date)
        if d > end_date:
            continue
        entries.append(ForecastEntry(d, -n.amount_home_currency, "debit", "pending_expense", n.category, n.description, n.event_id))
    return entries, excluded_income_ids


def _known_pool_for_dedup(state: FinancialState) -> List[NormalizedEvent]:
    """Every already-concrete future-relevant event a recurring occurrence
    could be a synthetic duplicate of."""
    return list(state.future_income_events) + list(state.future_expense_events) + list(state.pending_events)


def _find_dedup_match(pattern: RecurrencePattern, projected_date: date, pool: Sequence[NormalizedEvent]) -> Optional[NormalizedEvent]:
    """Conservative match: same direction + category within a date window
    scaled to the pattern's own cadence (capped at 7 days so a monthly
    pattern doesn't swallow an unrelated same-category event). When in
    doubt this returns a match (the caller then skips the synthetic
    occurrence) -- "prefer avoiding double counting" per the spec."""
    window = max(1, min(7, round(pattern.typical_interval_days / 2)))
    for n in pool:
        if n.direction != pattern.direction or n.category != pattern.category:
            continue
        if abs((_effective_date(n) - projected_date).days) <= window:
            return n
    return None


def _round_half_up(value: float) -> int:
    """Match financial_state.py's rounding convention: Python's round()
    uses round-half-to-even (round(30.5) == 30), which would silently
    shift a projected occurrence a day early. Calendar rounding always
    rounds .5 up."""
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


def _project_occurrences(pattern: RecurrencePattern, start_date: date, end_date: date) -> List[date]:
    """Roll the pattern's single `next_expected_occurrence` forward by its
    detected interval, into and then across the forecast window. Never
    invents a pattern Phase 2 didn't detect -- this only extrapolates an
    already-confirmed cadence."""
    if pattern.next_expected_occurrence is None:
        return []
    step_days = max(1, _round_half_up(pattern.typical_interval_days))
    occurrence = pattern.next_expected_occurrence
    guard = 0
    while occurrence < start_date and guard < 1000:
        occurrence += timedelta(days=step_days)
        guard += 1
    dates: List[date] = []
    while occurrence <= end_date and guard < 1000:
        dates.append(occurrence)
        occurrence += timedelta(days=step_days)
        guard += 1
    return dates


def _recurring_entries(state: FinancialState, start_date: date, end_date: date, warnings: List[str]) -> List[ForecastEntry]:
    entries: List[ForecastEntry] = []
    pool = _known_pool_for_dedup(state)
    for pattern in list(state.recurring_income_patterns) + list(state.recurring_expense_patterns):
        if pattern.typical_amount_home_currency is None:
            warnings.append(
                f"recurring {pattern.direction}/{pattern.category} pattern has no known typical amount "
                f"(all evidence occurrences had a blank amount); skipped rather than guessed"
            )
            continue
        source = "recurring_income" if pattern.direction == "credit" else "recurring_expense"
        for occurrence_date in _project_occurrences(pattern, start_date, end_date):
            if _find_dedup_match(pattern, occurrence_date, pool) is not None:
                continue  # a known future/pending event already represents this occurrence
            signed_amount = pattern.typical_amount_home_currency if pattern.direction == "credit" else -pattern.typical_amount_home_currency
            entries.append(ForecastEntry(
                occurrence_date, signed_amount, pattern.direction, source,
                pattern.category, pattern.description, None, pattern.source_event_ids,
            ))
    return entries


def _unresolved_relevant_event_ids(state: FinancialState, start_date: date, end_date: date) -> List[str]:
    ids: List[str] = []
    for n in state.unresolved_amount_events:
        if n.status not in ("scheduled", "pending"):
            continue  # historical (settled) unresolved amounts don't affect the future
        if start_date <= _effective_date(n) <= end_date:
            ids.append(n.event_id)
    return ids


def _entry_sort_key(entry: ForecastEntry):
    """A stable, deterministic display order for entries dated the same
    day (Part 10's "do not let dictionary/CSV ordering determine
    financial results"). This ordering is COSMETIC ONLY: the safety check
    itself nets an entire day's entries together and evaluates the
    balance once, at end-of-day (see `_simulate`'s docstring and the
    module docstring's "same-day cash flows" section) -- it never depends
    on which of two same-day entries is listed first.
    """
    return (entry.date, entry.category, entry.source, entry.event_id or "", entry.description)


def _collect_real_entries(
    state: FinancialState, start_date: date, end_date: date
) -> Tuple[List[ForecastEntry], List[str], List[str], List[str]]:
    """Gather every real (non-hypothetical) cash-flow entry relevant to
    [start_date, end_date], sorted per _entry_sort_key. Returns
    (entries, excluded_pending_income_ids, unresolved_relevant_ids, warnings)."""
    warnings: List[str] = []
    entries = _known_future_entries(state, start_date, end_date)
    pending_entries, excluded_income_ids = _pending_entries(state, start_date, end_date)
    entries += pending_entries
    entries += _recurring_entries(state, start_date, end_date, warnings)
    unresolved_ids = _unresolved_relevant_event_ids(state, start_date, end_date)
    entries.sort(key=_entry_sort_key)
    return entries, excluded_income_ids, unresolved_ids, warnings


def _simulate(
    state: FinancialState, start_date: date, end_date: date, hypotheticals: Sequence[ForecastEntry] = (),
) -> ForecastResult:
    real_entries, excluded_income_ids, unresolved_ids, warnings = _collect_real_entries(state, start_date, end_date)
    all_entries = list(real_entries) + list(hypotheticals)
    all_entries.sort(key=_entry_sort_key)

    minimum_to_keep = state.minimum_balance_to_keep
    balance = state.current_available_balance
    minimum_balance = balance
    minimum_date = start_date
    first_violation_date: Optional[date] = None
    violation_reason: Optional[str] = None

    if balance < minimum_to_keep:
        first_violation_date = start_date
        violation_reason = f"starting balance {balance} is already below the minimum {minimum_to_keep}"

    # Entries are recorded individually (for provenance/display), but the
    # safety check nets a whole calendar day together and evaluates the
    # balance once, at end-of-day -- see the module docstring's "same-day
    # cash flows" section for why.
    timeline: List[Tuple[date, Decimal]] = []
    index = 0
    total = len(all_entries)
    while index < total:
        day = all_entries[index].date
        day_entries = []
        while index < total and all_entries[index].date == day:
            day_entries.append(all_entries[index])
            index += 1
        for entry in day_entries:
            balance += entry.amount
            timeline.append((entry.date, balance))
        if balance < minimum_balance:
            minimum_balance = balance
            minimum_date = day
        if balance < minimum_to_keep and first_violation_date is None:
            first_violation_date = day
            net_change = sum(e.amount for e in day_entries)
            sign = "net credit" if net_change >= 0 else "net debit"
            violation_reason = (
                f"balance fell to {balance} by end of {day} after a {sign} of {abs(net_change)} "
                f"across {len(day_entries)} entr{'y' if len(day_entries) == 1 else 'ies'}"
            )

    return ForecastResult(
        user_id=state.user_id,
        home_currency=state.home_currency,
        starting_balance=state.current_available_balance,
        minimum_balance_to_keep=minimum_to_keep,
        forecast_start_date=start_date,
        forecast_end_date=end_date,
        entries=tuple(all_entries),
        balance_timeline=tuple(timeline),
        minimum_projected_balance=minimum_balance,
        minimum_projected_balance_date=minimum_date,
        is_safe=minimum_balance >= minimum_to_keep,
        first_violation_date=first_violation_date,
        violation_reason=violation_reason,
        excluded_pending_income_event_ids=tuple(excluded_income_ids),
        unresolved_relevant_event_ids=tuple(unresolved_ids),
        has_unresolved_evidence=bool(unresolved_ids),
        warnings=tuple(warnings),
        hypothetical_entries=tuple(hypotheticals),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_forecast(state: FinancialState, start_date: date, horizon_days: int = DEFAULT_HORIZON_DAYS) -> ForecastResult:
    """The baseline 90-day forecast with no hypothetical payment -- what
    would happen to this user's balance with zero new spending decisions."""
    end_date = start_date + timedelta(days=horizon_days)
    return _simulate(state, start_date, end_date, hypotheticals=())


def simulate_payments(
    state: FinancialState, start_date: date, payments: Sequence[Tuple[date, Decimal]],
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> ForecastResult:
    """Overlay one or more hypothetical debits onto the baseline forecast
    and re-simulate as ONE combined scenario. Required whenever a
    candidate plan has more than one payment (partial payment,
    installments): two payments that are each individually safe can still
    combine to breach the minimum balance, so a multi-payment plan must
    always be checked together, never as independent single-payment
    checks (Phase 4 Parts 19-20). `payments` need not be pre-sorted."""
    for payment_date, _ in payments:
        if payment_date < start_date:
            raise ValueError("every payment date must be on or after start_date")
    end_date = start_date + timedelta(days=horizon_days)
    hypotheticals = tuple(
        ForecastEntry(payment_date, -amount, "debit", "hypothetical_payment", "", f"hypothetical payment {i + 1}")
        for i, (payment_date, amount) in enumerate(payments)
    )
    return _simulate(state, start_date, end_date, hypotheticals=hypotheticals)


def simulate_payment(
    state: FinancialState, start_date: date, payment_date: date, amount: Decimal,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> ForecastResult:
    """Single-payment convenience wrapper over simulate_payments.
    `payment_date` may be any date within the horizon (not just
    `start_date`), which is what lets this same function answer both "is
    it safe to pay today" and "is it safe to pay on this future date"
    (Parts 11 and 12)."""
    return simulate_payments(state, start_date, [(payment_date, amount)], horizon_days)


def can_safely_pay(
    state: FinancialState, start_date: date, payment_date: date, amount: Decimal,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> ForecastResult:
    """Semantic alias of simulate_payment for the "is this safe?" call
    site -- read `.is_safe` for the yes/no answer and the rest of the
    ForecastResult for supporting detail. Chooses no payment method and
    applies no spending change; it only reports whether the hypothetical
    cash outflow itself is safe."""
    return simulate_payment(state, start_date, payment_date, amount, horizon_days)


def _day_end_balances_with_probe(
    state: FinancialState, start_date: date, end_date: date, probe_date: date,
    prior_payments: Sequence[Tuple[date, Decimal]] = (),
) -> Tuple[List[Tuple[date, Decimal]], int]:
    """Simulate real entries + any already-committed prior payments + a
    zero-amount probe at `probe_date`, netting each calendar day together
    (per the module's same-day rule), and return
    `(day_end_balances, probe_day_index)` -- the balance at the end of
    every day that has at least one entry (including the probe's own
    day), and the index of the probe's day within that list."""
    real_entries, _, _, _ = _collect_real_entries(state, start_date, end_date)
    prior_entries = [
        ForecastEntry(d, -amount, "debit", "hypothetical_payment", "", f"prior payment {i + 1}")
        for i, (d, amount) in enumerate(prior_payments)
    ]
    probe = ForecastEntry(probe_date, Decimal(0), "debit", "probe", "", "probe")
    combined = list(real_entries) + prior_entries + [probe]
    combined.sort(key=_entry_sort_key)

    balance = state.current_available_balance
    day_end_balances: List[Tuple[date, Decimal]] = []
    probe_day_index: Optional[int] = None
    index = 0
    total = len(combined)
    while index < total:
        day = combined[index].date
        is_probe_day = False
        while index < total and combined[index].date == day:
            balance += combined[index].amount
            is_probe_day = is_probe_day or combined[index].source == "probe"
            index += 1
        day_end_balances.append((day, balance))
        if is_probe_day:
            probe_day_index = len(day_end_balances) - 1
    assert probe_day_index is not None  # the probe is always in `combined`
    return day_end_balances, probe_day_index


def maximum_safe_payment(
    state: FinancialState, start_date: date, payment_date: date, horizon_days: int = DEFAULT_HORIZON_DAYS,
    prior_payments: Sequence[Tuple[date, Decimal]] = (),
) -> Decimal:
    """The largest amount payable on `payment_date` without ever breaching
    `minimum_balance_to_keep` between `payment_date` and the end of the
    forecast horizon. Derived in closed form: insert a zero-amount "probe"
    entry at `payment_date`, net every calendar day together exactly as
    `_simulate` does, then the answer is the minimum END-OF-DAY balance
    from the probe's day onward, minus the minimum-balance floor, floored
    at zero. No dollar-by-dollar search.

    `prior_payments` lets a caller ask "how much more can safely be paid
    on `payment_date`, GIVEN that these other payments are already
    committed as part of the same plan" -- e.g. a partial payment's second
    leg must be sized with the first leg already applied, never against a
    forecast that has forgotten it (Part 20).
    """
    if payment_date < start_date:
        raise ValueError("payment_date must be on or after start_date")
    for d, _ in prior_payments:
        if d < start_date:
            raise ValueError("every prior payment date must be on or after start_date")
    end_date = start_date + timedelta(days=horizon_days)
    day_end_balances, probe_day_index = _day_end_balances_with_probe(
        state, start_date, end_date, payment_date, prior_payments)
    minimum_from_payment_onward = min(balance for _, balance in day_end_balances[probe_day_index:])
    headroom = minimum_from_payment_onward - state.minimum_balance_to_keep
    return headroom if headroom > 0 else Decimal(0)


def earliest_safe_payment_date(
    state: FinancialState, start_date: date, amount: Decimal,
    deadline: Optional[date] = None, horizon_days: int = DEFAULT_HORIZON_DAYS,
    prior_payments: Sequence[Tuple[date, Decimal]] = (),
) -> Optional[date]:
    """The earliest date on which paying `amount` is safe through the full
    forecast horizon (NOT clipped to `deadline` for the safety check
    itself -- only the search range is optionally clipped by `deadline`).
    Returns None if no such date exists within the searched range.

    This is the raw baseline fact behind `earliest_date_for_full_payment`
    (Part 16): computed with no spending changes, no payment-method
    preference, and no installment options -- only the confirmed/
    forecastable financial state.

    `maximum_safe_payment(D)` is provably a step function of D that can
    only change value exactly ON a date with at least one real entry or
    prior payment (since same-day flows are netted together -- a probe
    placed on any such date sees that whole day's net change), so it
    suffices to test each such date once, in order, rather than every
    calendar day -- bounded by this one user's own entry count, not a
    fixed-size brute-force scan.

    `prior_payments`: see `maximum_safe_payment` -- lets a caller search
    for the earliest safe date for a SECOND (or later) leg of a
    multi-payment plan, with the earlier leg(s) already committed.
    """
    end_date = start_date + timedelta(days=horizon_days)
    search_end = min(deadline, end_date) if deadline is not None else end_date
    if search_end < start_date:
        return None

    real_entries, _, _, _ = _collect_real_entries(state, start_date, end_date)
    candidate_dates = sorted(
        {start_date} | {e.date for e in real_entries} | {d for d, _ in prior_payments}
    )
    candidate_dates = [d for d in candidate_dates if start_date <= d <= search_end]

    for candidate in candidate_dates:
        if maximum_safe_payment(state, start_date, candidate, horizon_days, prior_payments=prior_payments) >= amount:
            return candidate
    return None


def describe_payment_safety(
    state: FinancialState, start_date: date, amount: Decimal,
    deadline: Optional[date] = None, horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> PaymentSafetyProfile:
    """Convenience aggregation of the three raw facts Part 13 asks for:
    safe_now, first_safe_date, safe_by_deadline. Deliberately returns
    facts, not a decision -- Phase 4 turns these into affordability_status."""
    horizon_end = start_date + timedelta(days=horizon_days)
    now_result = can_safely_pay(state, start_date, start_date, amount, horizon_days)
    first_safe = earliest_safe_payment_date(state, start_date, amount, deadline=None, horizon_days=horizon_days)
    safe_by_deadline = bool(first_safe is not None and deadline is not None and first_safe <= deadline)
    return PaymentSafetyProfile(
        amount=amount,
        start_date=start_date,
        deadline=deadline,
        horizon_end_date=horizon_end,
        safe_now=now_result.is_safe,
        first_safe_date=first_safe,
        safe_by_deadline=safe_by_deadline,
    )
