"""Deterministic financial-state reconstruction (Phase 2).

Turns the raw, per-user `FinancialEvent` rows from `data_loader.DataStore`
into a reconciled `FinancialState`: every event is currency-normalized and
classified by its actual cash-flow effect, linked-event pairs are validated
and labelled (without ever assuming a link voids the original row), and
conservative recurrence detection surfaces recurring income/expense
patterns from settled history only.

Explicitly out of scope here (deferred to later phases): 90-day
forecasting, amount_safe_to_pay, payment-plan generation, spending-change
optimization, plan ranking, output.csv generation, and any LLM/vision use.

Core design rule carried over from the problem statement: "the link alone
does not determine whether a row counts toward cash flow." Every event's
own `status` + `direction` decides its `EventTreatment` -- linked-event
analysis and conflict resolution are informational/audit layers on top,
never overrides of an individual row's classification.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Tuple

from currency import CurrencyConversionError, CurrencyConverter
from data_loader import DataStore, FinancialEvent

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class EventTreatment(Enum):
    """How one financial_events.csv row affects (or doesn't affect) cash."""

    EFFECTIVE_INCOME = "effective_income"      # settled credit, known amount -- real historical inflow
    EFFECTIVE_EXPENSE = "effective_expense"    # settled debit, known amount -- real historical outflow
    PENDING_INCOME = "pending_income"          # pending credit -- NOT yet available cash
    PENDING_EXPENSE = "pending_expense"        # pending debit -- reserve it, hasn't left yet
    SCHEDULED_INCOME = "scheduled_income"      # scheduled credit -- known future income (e.g. next salary)
    SCHEDULED_EXPENSE = "scheduled_expense"    # scheduled debit -- known future outflow
    CANCELLED = "cancelled"                    # never happened -- no cash effect
    FAILED = "failed"                          # never happened -- no cash effect
    NON_CASH = "non_cash"                      # investment valuation / unrealized -- informational only
    UNRESOLVED_AMOUNT = "unresolved_amount"    # amount is (or became) unknown -- never treated as zero
    UNKNOWN = "unknown"                        # status/direction combo not in the observed schema


#: Statuses actually observed in dataset/financial_events.csv (Phase 0/1).
#: A status outside this set is not silently guessed at -- it is routed to
#: EventTreatment.UNKNOWN with a warning (see classify_treatment).
KNOWN_STATUSES = {"settled", "cancelled", "pending", "scheduled", "failed", "unrealized"}
KNOWN_DIRECTIONS = {"debit", "credit", "non_cash"}


class LinkedRelationship(Enum):
    """How a `linked_event_id` pair relates, purely for diagnostics/audit --
    never used to change either row's own EventTreatment."""

    REFUND_OF_EXPENSE = "refund_of_expense"
    REPLACEMENT_AFTER_CANCELLATION = "replacement_after_cancellation"
    RESCHEDULED_AFTER_FAILURE = "rescheduled_after_failure"
    INVESTMENT_VALUATION_UPDATE = "investment_valuation_update"
    INVESTMENT_SALE = "investment_sale"
    FOLLOWUP_OR_SPLIT_PAYMENT = "followup_or_split_payment"
    UNKNOWN_PATTERN = "unknown_pattern"


class ConflictTier(Enum):
    """Which rule from the problem statement's conflict-resolution
    priority list decided a winner between competing records."""

    EXPLICIT_STATUS = "explicit_cancellation_settlement_amendment"
    NEWER_SAME_SOURCE = "newer_record_same_source"
    SETTLED_OVER_ESTIMATE = "settled_over_estimate"
    SAFER_INTERPRETATION = "safer_interpretation"


class RecurrenceFrequency(Enum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LinkedEventInfo:
    linked_event_id: str
    original: Optional[FinancialEvent]  # None if the reference doesn't resolve
    relationship: LinkedRelationship
    issues: Tuple[str, ...] = ()  # "missing_reference" | "cross_user_reference" | "out_of_order_reference"


@dataclass(frozen=True)
class NormalizedEvent:
    """One financial_events.csv row, currency-normalized and classified.

    The original row is preserved in full via ``raw_event`` -- nothing is
    discarded, only annotated.
    """

    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    original_amount: Optional[Decimal]
    original_currency: str
    amount_home_currency: Optional[Decimal]  # None iff original_amount is None or conversion failed
    event_date: date
    settlement_date: Optional[date]
    status: str
    linked_event_id: Optional[str]
    flexibility: str
    minimum_allowed_amount: Optional[Decimal]
    treatment: EventTreatment
    linked_info: Optional[LinkedEventInfo]
    raw_event: FinancialEvent


@dataclass(frozen=True)
class RecurrencePattern:
    user_id: str
    event_type: str
    direction: str
    category: str
    description: str  # most common description among the evidence events
    frequency: RecurrenceFrequency
    typical_interval_days: float
    typical_amount_home_currency: Optional[Decimal]
    home_currency: str
    next_expected_occurrence: Optional[date]
    occurrence_count: int
    confidence: str  # "high" | "medium"
    flexibility: str
    minimum_allowed_amount: Optional[Decimal]
    source_event_ids: Tuple[str, ...]


@dataclass(frozen=True)
class ConflictResolutionRecord:
    winner_event_id: str
    loser_event_ids: Tuple[str, ...]
    tier: ConflictTier
    reason: str


@dataclass
class FinancialState:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal

    # Effective (settled, known-amount) historical events -- income + expense mixed,
    # distinguish via .direction / .treatment.
    effective_events: List[NormalizedEvent] = field(default_factory=list)
    pending_events: List[NormalizedEvent] = field(default_factory=list)
    future_income_events: List[NormalizedEvent] = field(default_factory=list)
    future_expense_events: List[NormalizedEvent] = field(default_factory=list)
    cancelled_or_failed_events: List[NormalizedEvent] = field(default_factory=list)
    non_cash_events: List[NormalizedEvent] = field(default_factory=list)
    unresolved_amount_events: List[NormalizedEvent] = field(default_factory=list)

    recurring_income_patterns: List[RecurrencePattern] = field(default_factory=list)
    recurring_expense_patterns: List[RecurrencePattern] = field(default_factory=list)

    # Profile policy fields, carried through unchanged for later phases.
    financial_priorities: List[str] = field(default_factory=list)
    expense_categories_to_protect: List[str] = field(default_factory=list)
    expense_categories_user_is_willing_to_reduce: List[str] = field(default_factory=list)
    expense_categories_user_is_willing_to_stop: List[str] = field(default_factory=list)
    payment_methods_user_will_consider: List[str] = field(default_factory=list)
    max_installment_months: Optional[int] = None

    linked_event_issues: List[str] = field(default_factory=list)
    conflict_resolutions: List[ConflictResolutionRecord] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    #: Every event for this user, normalized, regardless of bucket -- the
    #: full audit trail. The buckets above are convenience views over this.
    all_normalized_events: List[NormalizedEvent] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Event classification
# ---------------------------------------------------------------------------


def _effective_date(ev: FinancialEvent) -> date:
    return ev.settlement_date or ev.event_date


def classify_treatment(ev: FinancialEvent, warnings: List[str]) -> EventTreatment:
    """Classify one event's cash-flow effect from its own fields only.

    Order matters and is deliberate: a cancelled/failed row has no cash
    effect regardless of whether its amount happens to be known, and
    non-cash/unrealized rows are informational regardless of direction.
    Only after those are ruled out does a blank amount become the reason
    an otherwise-real row can't be classified as effective/pending/etc.
    """
    if ev.status == "cancelled":
        return EventTreatment.CANCELLED
    if ev.status == "failed":
        return EventTreatment.FAILED
    if ev.direction == "non_cash" or ev.status == "unrealized":
        return EventTreatment.NON_CASH
    if ev.amount is None:
        return EventTreatment.UNRESOLVED_AMOUNT
    if ev.status == "settled" and ev.direction == "credit":
        return EventTreatment.EFFECTIVE_INCOME
    if ev.status == "settled" and ev.direction == "debit":
        return EventTreatment.EFFECTIVE_EXPENSE
    if ev.status == "pending" and ev.direction == "credit":
        return EventTreatment.PENDING_INCOME
    if ev.status == "pending" and ev.direction == "debit":
        return EventTreatment.PENDING_EXPENSE
    if ev.status == "scheduled" and ev.direction == "credit":
        return EventTreatment.SCHEDULED_INCOME
    if ev.status == "scheduled" and ev.direction == "debit":
        return EventTreatment.SCHEDULED_EXPENSE

    warnings.append(
        f"{ev.event_id}: unrecognized status/direction combination "
        f"(status={ev.status!r}, direction={ev.direction!r}); treated conservatively as UNKNOWN "
        f"and excluded from every cash-flow bucket"
    )
    return EventTreatment.UNKNOWN


# ---------------------------------------------------------------------------
# Linked-event analysis
# ---------------------------------------------------------------------------


def classify_linked_relationship(original: FinancialEvent, current: FinancialEvent) -> LinkedRelationship:
    """Label how ``current`` relates to the earlier ``original`` event it
    links to. This label is informational only -- it never changes either
    event's own EventTreatment, which always comes from that row's own
    status/direction (classify_treatment)."""
    key = (original.event_type, current.event_type)
    if key == ("expense", "refund"):
        return LinkedRelationship.REFUND_OF_EXPENSE
    if key == ("investment_purchase", "investment_valuation"):
        return LinkedRelationship.INVESTMENT_VALUATION_UPDATE
    if key == ("investment_purchase", "investment_sale"):
        return LinkedRelationship.INVESTMENT_SALE
    if key == ("expense", "expense"):
        if original.status == "cancelled" and current.status == "settled":
            return LinkedRelationship.REPLACEMENT_AFTER_CANCELLATION
        if original.status == "settled" and current.status == "pending":
            return LinkedRelationship.FOLLOWUP_OR_SPLIT_PAYMENT
        return LinkedRelationship.UNKNOWN_PATTERN
    if key == ("debt_payment", "debt_payment"):
        if original.status == "failed" and current.status == "scheduled":
            return LinkedRelationship.RESCHEDULED_AFTER_FAILURE
        return LinkedRelationship.UNKNOWN_PATTERN
    return LinkedRelationship.UNKNOWN_PATTERN


def resolve_linked_event(
    current: FinancialEvent, store: DataStore, warnings: List[str]
) -> Optional[LinkedEventInfo]:
    """Look up and validate ``current.linked_event_id`` via the DataStore
    index. Never raises -- a broken link is recorded as an issue + warning,
    and ``current``'s own classification is completely unaffected."""
    if not current.linked_event_id:
        return None

    original = store.get_event(current.linked_event_id)
    issues: List[str] = []

    if original is None:
        issues.append("missing_reference")
        warnings.append(
            f"{current.event_id}: linked_event_id {current.linked_event_id!r} does not exist"
        )
        return LinkedEventInfo(current.linked_event_id, None, LinkedRelationship.UNKNOWN_PATTERN, tuple(issues))

    if original.user_id != current.user_id:
        issues.append("cross_user_reference")
        warnings.append(
            f"{current.event_id}: linked_event_id {current.linked_event_id!r} belongs to a "
            f"different user ({original.user_id!r} vs {current.user_id!r}); ignoring for reconciliation"
        )

    if original.event_date > current.event_date:
        issues.append("out_of_order_reference")
        warnings.append(
            f"{current.event_id}: linked_event_id {current.linked_event_id!r} has a later "
            f"event_date ({original.event_date}) than the linking event ({current.event_date})"
        )

    relationship = classify_linked_relationship(original, current)
    if relationship == LinkedRelationship.UNKNOWN_PATTERN and not issues:
        warnings.append(
            f"{current.event_id}: linked relationship with {original.event_id} "
            f"({original.event_type}/{original.status} -> {current.event_type}/{current.status}) "
            f"did not match a known pattern; recorded as UNKNOWN_PATTERN"
        )
    return LinkedEventInfo(current.linked_event_id, original, relationship, tuple(issues))


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------

_DEFINITIVE_STATUSES = {"settled", "cancelled", "failed"}


def resolve_conflict(
    candidates: List[FinancialEvent],
) -> Tuple[FinancialEvent, ConflictTier, List[FinancialEvent]]:
    """Pick the financially-effective record among candidates that
    describe **the same underlying fact** (e.g. two records both claiming
    to be the current amount/status of one transaction), following the
    problem statement's priority order:

        1. an explicit cancellation/settlement/amendment
        2. a newer record from the same source
        3. a settled event over an estimate/forecast
        4. the financially safer interpretation, as a last resort

    Returns ``(winner, tier, losers)``. Every candidate is preserved by the
    caller regardless -- this function only says which one is *effective*,
    it does not delete anything.

    Important scoping note found during Phase 2: none of this dataset's 58
    ``linked_event_id`` pairs are actually competing descriptions of one
    fact -- a refund is independent of its original expense, an investment
    valuation is independent of its purchase, and (less obviously) a
    failed payment attempt and its rescheduled retry are also two
    independently-true sequential facts, not two answers to "did this
    payment happen?". Calling ``resolve_conflict`` on that last pair would
    misleadingly declare the *failed* attempt "more authoritative" than
    the live rescheduled one (failed is as explicit/definitive a status as
    settled, per tier 1) -- which is correct as a generic same-fact
    resolution, but wrong to apply here, because there is no shared fact
    to resolve. See ``test_financial_state.py``'s
    ``test_resolve_conflict_would_misjudge_the_failed_then_rescheduled_pair``
    for the demonstration. Consequently ``build_financial_state`` does
    NOT call this function automatically for any of the observed linked
    patterns -- it stays available, tested, and ready for Phase 3, where
    message/image evidence can create genuine same-fact conflicts (e.g. a
    message amending an amount the ledger already recorded differently).
    """
    if not candidates:
        raise ValueError("resolve_conflict requires at least one candidate")
    if len(candidates) == 1:
        return candidates[0], ConflictTier.EXPLICIT_STATUS, []

    def losers_of(winner: FinancialEvent) -> List[FinancialEvent]:
        return [c for c in candidates if c is not winner]

    # Tier 1: a definitive outcome (settled/cancelled/failed) beats a mere
    # estimate (pending/scheduled) of the same fact.
    definitive = [c for c in candidates if c.status in _DEFINITIVE_STATUSES]
    pool = definitive if definitive else list(candidates)
    if len(pool) == 1:
        return pool[0], ConflictTier.EXPLICIT_STATUS, losers_of(pool[0])

    # Tier 2: among remaining ties, the newer record (by settlement/event date) wins.
    newest_date = max(_effective_date(c) for c in pool)
    newest = [c for c in pool if _effective_date(c) == newest_date]
    if len(newest) == 1:
        return newest[0], ConflictTier.NEWER_SAME_SOURCE, losers_of(newest[0])
    pool = newest

    # Tier 3: settled beats a remaining non-settled estimate.
    settled = [c for c in pool if c.status == "settled"]
    if len(settled) == 1:
        return settled[0], ConflictTier.SETTLED_OVER_ESTIMATE, losers_of(settled[0])
    if settled:
        pool = settled
    if len(pool) == 1:
        return pool[0], ConflictTier.SETTLED_OVER_ESTIMATE, losers_of(pool[0])

    # Tier 4: last resort -- assume the financially safer amount (bigger
    # debit, smaller/no credit) so an unresolved tie never overstates
    # what's safe to spend.
    safest = _pick_safer_candidate(pool)
    return safest, ConflictTier.SAFER_INTERPRETATION, losers_of(safest)


def _pick_safer_candidate(pool: List[FinancialEvent]) -> FinancialEvent:
    def risk_key(c: FinancialEvent) -> Decimal:
        amount = c.amount if c.amount is not None else Decimal("Infinity")
        # A bigger debit is the more conservative assumption (reserves more
        # cash); a smaller credit is the more conservative assumption
        # (counts on less). Negating debit amounts lets min() do both.
        return -amount if c.direction == "debit" else amount

    return min(pool, key=risk_key)


# ---------------------------------------------------------------------------
# Currency normalization + per-event assembly
# ---------------------------------------------------------------------------


def normalize_event(
    ev: FinancialEvent,
    store: DataStore,
    converter: CurrencyConverter,
    home_currency: str,
    warnings: List[str],
) -> NormalizedEvent:
    treatment = classify_treatment(ev, warnings)

    amount_home_currency: Optional[Decimal]
    if ev.amount is None:
        amount_home_currency = None
    elif ev.currency == home_currency:
        amount_home_currency = ev.amount
    else:
        on_date = _effective_date(ev)
        try:
            amount_home_currency = converter.convert(ev.amount, ev.currency, home_currency, on_date)
        except CurrencyConversionError as exc:
            amount_home_currency = None
            treatment = EventTreatment.UNRESOLVED_AMOUNT
            warnings.append(
                f"{ev.event_id}: could not convert {ev.currency}->{home_currency} on {on_date}: "
                f"{exc}; amount left unresolved rather than guessed"
            )

    linked_info = resolve_linked_event(ev, store, warnings)

    return NormalizedEvent(
        event_id=ev.event_id,
        user_id=ev.user_id,
        event_type=ev.event_type,
        description=ev.description,
        category=ev.category,
        direction=ev.direction,
        original_amount=ev.amount,
        original_currency=ev.currency,
        amount_home_currency=amount_home_currency,
        event_date=ev.event_date,
        settlement_date=ev.settlement_date,
        status=ev.status,
        linked_event_id=ev.linked_event_id,
        flexibility=ev.flexibility,
        minimum_allowed_amount=ev.minimum_allowed_amount,
        treatment=treatment,
        linked_info=linked_info,
        raw_event=ev,
    )


# ---------------------------------------------------------------------------
# Recurrence detection
# ---------------------------------------------------------------------------

#: (lower_days, upper_days) tolerance bands for each calendar cadence.
#: Calibrated against the actual dataset (Phase 2 recon): real recurring
#: groups show near-zero gap variance, so these bands are generous on
#: purpose -- wide enough for weekends/month-length noise, never so wide
#: that two different cadences could collide.
_CADENCE_BANDS: List[Tuple[RecurrenceFrequency, Tuple[float, float]]] = [
    (RecurrenceFrequency.WEEKLY, (6.0, 9.0)),
    (RecurrenceFrequency.BIWEEKLY, (12.0, 16.0)),
    (RecurrenceFrequency.MONTHLY, (25.0, 34.0)),
    (RecurrenceFrequency.QUARTERLY, (80.0, 100.0)),
    (RecurrenceFrequency.ANNUAL, (350.0, 380.0)),
]

#: Conservative thresholds: at least 3 occurrences, and the gaps between
#: them must be regular (coefficient of variation <= 0.35) before anything
#: is called "recurring". A one-off transaction (n=1) can never qualify.
MIN_RECURRENCE_OCCURRENCES = 3
MAX_GAP_COEFFICIENT_OF_VARIATION = 0.35


def _round_half_up(value: float) -> int:
    """Python's builtin round() uses round-half-to-even ("banker's
    rounding"): round(30.5) == 30, not 31. For calendar-date arithmetic
    that silently shifts a projected occurrence a day early whenever a
    pattern's mean gap lands on an exact .5 (e.g. alternating 30/31-day
    months). Calendar rounding should always round .5 up.
    """
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


def _classify_cadence(mean_gap_days: float) -> Optional[RecurrenceFrequency]:
    for frequency, (lo, hi) in _CADENCE_BANDS:
        if lo <= mean_gap_days <= hi:
            return frequency
    return None


def _median_decimal(values: List[Decimal]) -> Decimal:
    values_sorted = sorted(values)
    n = len(values_sorted)
    mid = n // 2
    if n % 2 == 1:
        return values_sorted[mid]
    return (values_sorted[mid - 1] + values_sorted[mid]) / Decimal(2)


def _longest_dominant_cadence_run(dates: List[date]) -> List[int]:
    """Given chronologically sorted dates for one (event_type, direction,
    category) group, find the single calendar cadence that the most
    consecutive gaps agree on, then return the indices of the longest
    unbroken run of dates connected by a gap matching that cadence.

    This exists because a handful of one-off, differently-timed entries
    in an otherwise clearly-periodic category (a bonus, an arrears
    correction, a same-day duplicate) must not poison detection of the
    real pattern -- found via real-dataset validation in Phase 3: e.g. a
    user with 5 clean monthly "Payroll credit" rows plus one same-month
    "Promotion arrears payment" and one same-month restated salary row
    previously caused the *entire* salary category to be rejected as
    "irregular", even though 5 of its 7 occurrences were perfectly
    monthly. Returns the indices of the best run (>= 2 dates), or an
    empty list if no gap matches any recognized cadence at all.
    """
    n = len(dates)
    if n < 2:
        return []

    gap_bucket: List[Optional[RecurrenceFrequency]] = []
    for i in range(n - 1):
        gap_days = (dates[i + 1] - dates[i]).days
        gap_bucket.append(_classify_cadence(float(gap_days)) if gap_days > 0 else None)

    bucket_counts = Counter(b for b in gap_bucket if b is not None)
    if not bucket_counts:
        return []
    dominant_bucket = bucket_counts.most_common(1)[0][0]

    best_run: List[int] = []
    current_run = [0]
    for i, bucket in enumerate(gap_bucket):
        if bucket == dominant_bucket:
            current_run.append(i + 1)
        else:
            if len(current_run) > len(best_run):
                best_run = current_run
            current_run = [i + 1]
    if len(current_run) > len(best_run):
        best_run = current_run
    return best_run if len(best_run) >= 2 else []


def detect_recurrence_patterns(
    settled_events: List[FinancialEvent],
    converter: CurrencyConverter,
    home_currency: str,
) -> List[RecurrencePattern]:
    """Conservatively detect recurring income/expense patterns for one
    user from their *settled* event history.

    Groups events by ``(event_type, direction, category)`` -- never by
    description text, so a one-off transaction cannot inherit recurrence
    just because its wording resembles an unrelated recurring bill. Within
    a group, only the longest run of occurrences that agree on a single
    calendar cadence is used as evidence (see
    `_longest_dominant_cadence_run`) -- a handful of unrelated same-category
    one-offs (bonuses, corrections) are excluded from that run rather than
    invalidating the whole group. A pattern is only produced when that run
    has >= MIN_RECURRENCE_OCCURRENCES dates and the run's own gaps are
    regular enough (coefficient of variation <= MAX_GAP_COEFFICIENT_OF_VARIATION).
    """
    settled = [e for e in settled_events if e.status == "settled"]
    if not settled:
        return []
    user_id = settled[0].user_id

    groups: Dict[Tuple[str, str, str], List[FinancialEvent]] = defaultdict(list)
    for ev in settled:
        groups[(ev.event_type, ev.direction, ev.category)].append(ev)

    patterns: List[RecurrencePattern] = []
    for (event_type, direction, category), evs in sorted(groups.items()):
        if len(evs) < MIN_RECURRENCE_OCCURRENCES:
            continue

        evs_sorted = sorted(evs, key=lambda e: (_effective_date(e), e.event_id))
        dates = [_effective_date(e) for e in evs_sorted]

        run_indices = _longest_dominant_cadence_run(dates)
        if len(run_indices) < MIN_RECURRENCE_OCCURRENCES:
            continue
        core_events = [evs_sorted[i] for i in run_indices]
        core_dates = [dates[i] for i in run_indices]

        gaps = [(core_dates[i + 1] - core_dates[i]).days for i in range(len(core_dates) - 1)]
        mean_gap = statistics.mean(gaps)
        stdev_gap = statistics.pstdev(gaps) if len(gaps) > 1 else 0.0
        coefficient_of_variation = (stdev_gap / mean_gap) if mean_gap else float("inf")
        if coefficient_of_variation > MAX_GAP_COEFFICIENT_OF_VARIATION:
            continue

        frequency = _classify_cadence(mean_gap)
        if frequency is None:
            continue

        known_amounts: List[Decimal] = []
        for e in core_events:
            if e.amount is None:
                continue
            try:
                known_amounts.append(converter.convert(e.amount, e.currency, home_currency, _effective_date(e)))
            except CurrencyConversionError:
                continue
        typical_amount = _median_decimal(known_amounts) if known_amounts else None

        representative_description = Counter(e.description for e in core_events).most_common(1)[0][0]
        representative_flexibility = Counter(e.flexibility for e in core_events).most_common(1)[0][0]
        latest_minimum_allowed = next(
            (e.minimum_allowed_amount for e in reversed(core_events) if e.minimum_allowed_amount is not None),
            None,
        )
        confidence = "high" if len(core_events) >= 5 and coefficient_of_variation <= 0.15 else "medium"

        patterns.append(RecurrencePattern(
            user_id=user_id,
            event_type=event_type,
            direction=direction,
            category=category,
            description=representative_description,
            frequency=frequency,
            typical_interval_days=round(mean_gap, 1),
            typical_amount_home_currency=typical_amount,
            home_currency=home_currency,
            next_expected_occurrence=core_dates[-1] + timedelta(days=_round_half_up(mean_gap)),
            occurrence_count=len(core_events),
            confidence=confidence,
            flexibility=representative_flexibility,
            minimum_allowed_amount=latest_minimum_allowed,
            source_event_ids=tuple(e.event_id for e in core_events),
        ))
    return patterns


# ---------------------------------------------------------------------------
# Per-user FinancialState assembly
# ---------------------------------------------------------------------------


def build_financial_state(store: DataStore, converter: CurrencyConverter, user_id: str) -> FinancialState:
    """Build the normalized, reconciled FinancialState for one user.

    Uses only ``store.get_events_for_user(user_id)`` (an O(1) index
    lookup) -- never scans the full 25k-row event table.
    """
    profile = store.get_profile(user_id)
    if profile is None:
        raise ValueError(f"no financial profile found for user_id={user_id!r}")

    raw_events_sorted = sorted(store.get_events_for_user(user_id), key=lambda e: (e.event_date, e.event_id))

    warnings: List[str] = []
    normalized = [
        normalize_event(ev, store, converter, profile.home_currency, warnings)
        for ev in raw_events_sorted
    ]

    state = FinancialState(
        user_id=user_id,
        home_currency=profile.home_currency,
        current_available_balance=profile.current_available_balance,
        minimum_balance_to_keep=profile.minimum_balance_to_keep,
        financial_priorities=list(profile.financial_priorities),
        expense_categories_to_protect=list(profile.expense_categories_to_protect),
        expense_categories_user_is_willing_to_reduce=list(profile.expense_categories_user_is_willing_to_reduce),
        expense_categories_user_is_willing_to_stop=list(profile.expense_categories_user_is_willing_to_stop),
        payment_methods_user_will_consider=list(profile.payment_methods_user_will_consider),
        max_installment_months=profile.max_installment_months,
        all_normalized_events=normalized,
    )

    _BUCKETS = {
        EventTreatment.EFFECTIVE_INCOME: state.effective_events,
        EventTreatment.EFFECTIVE_EXPENSE: state.effective_events,
        EventTreatment.PENDING_INCOME: state.pending_events,
        EventTreatment.PENDING_EXPENSE: state.pending_events,
        EventTreatment.SCHEDULED_INCOME: state.future_income_events,
        EventTreatment.SCHEDULED_EXPENSE: state.future_expense_events,
        EventTreatment.CANCELLED: state.cancelled_or_failed_events,
        EventTreatment.FAILED: state.cancelled_or_failed_events,
        EventTreatment.NON_CASH: state.non_cash_events,
        EventTreatment.UNRESOLVED_AMOUNT: state.unresolved_amount_events,
        EventTreatment.UNKNOWN: state.unresolved_amount_events,
    }

    # Note: linked-event pairs are recorded as informational relationship
    # metadata only (below), never fed through resolve_conflict() here --
    # see that function's docstring for why none of this dataset's linked
    # patterns are genuine same-fact conflicts. state.conflict_resolutions
    # stays available for Phase 3, when evidence can create real conflicts.
    for n in normalized:
        _BUCKETS[n.treatment].append(n)
        if n.linked_info and n.linked_info.issues:
            state.linked_event_issues.append(
                f"{n.event_id} -> {n.linked_info.linked_event_id}: {', '.join(n.linked_info.issues)}"
            )

    # Recurrence evidence: every settled event regardless of whether its
    # amount happens to be known (a blank-amount month shouldn't break an
    # otherwise-clear cadence) -- amount-based stats simply skip unknowns.
    settled_raw = [n.raw_event for n in normalized if n.raw_event.status == "settled"]
    for pattern in detect_recurrence_patterns(settled_raw, converter, profile.home_currency):
        if pattern.direction == "credit":
            state.recurring_income_patterns.append(pattern)
        else:
            state.recurring_expense_patterns.append(pattern)

    # A `scheduled` income event -- the data dictionary's "next confirmed
    # salary" -- is confirmed future income in its own right, not a mere
    # estimate, and salary is domain-conventionally periodic. Anchor an
    # ongoing recurring pattern on it even when settled history alone is
    # too thin to clear detect_recurrence_patterns' >=3-occurrence bar
    # (e.g. a recently-hired user with only one or two prior settled
    # salary payments). Found via real-dataset validation in Phase 3:
    # without this, a new employee's 90-day forecast runs out of income
    # after their one already-scheduled paycheck, even though the
    # problem's own sample answers assume salary continues (see
    # eval/phase3_report.md for the concrete example). Skipped for a
    # category a fuller, evidence-based pattern already covers.
    covered_income_categories = {p.category for p in state.recurring_income_patterns}
    for scheduled in state.future_income_events:
        if scheduled.category in covered_income_categories:
            continue
        prior_same_category = [
            n for n in state.effective_events
            if n.direction == "credit" and n.category == scheduled.category
        ]
        anchored = _anchor_confirmed_income_pattern(scheduled, prior_same_category, profile.home_currency)
        if anchored is not None:
            state.recurring_income_patterns.append(anchored)
            covered_income_categories.add(scheduled.category)

    state.warnings = warnings
    return state


def _anchor_confirmed_income_pattern(
    scheduled: NormalizedEvent,
    prior_effective_same_category: List[NormalizedEvent],
    home_currency: str,
) -> Optional[RecurrencePattern]:
    """Synthesize a recurring-income pattern anchored on a single
    `scheduled` ("confirmed") income event. Uses the gap to the most
    recent prior settled occurrence of the same category as the interval
    when that gap matches a recognized cadence; otherwise (or with no
    prior occurrence at all) defaults to monthly, since that is the
    conventional cadence for salary income. Returns None only if the
    scheduled event's own amount is unresolved -- never guesses a value.
    """
    if scheduled.amount_home_currency is None:
        return None
    prior = sorted(prior_effective_same_category, key=lambda n: n.event_date)
    if prior:
        gap_days = (scheduled.event_date - prior[-1].event_date).days
        frequency = _classify_cadence(float(gap_days))
        interval_days = float(gap_days) if frequency is not None else 30.0
        frequency = frequency or RecurrenceFrequency.MONTHLY
    else:
        frequency = RecurrenceFrequency.MONTHLY
        interval_days = 30.0
    source_event_ids = tuple(n.event_id for n in prior) + (scheduled.event_id,)
    return RecurrencePattern(
        user_id=scheduled.user_id,
        event_type=scheduled.event_type,
        direction=scheduled.direction,
        category=scheduled.category,
        description=scheduled.description,
        frequency=frequency,
        typical_interval_days=interval_days,
        typical_amount_home_currency=scheduled.amount_home_currency,
        home_currency=home_currency,
        next_expected_occurrence=scheduled.event_date + timedelta(days=_round_half_up(interval_days)),
        occurrence_count=len(source_event_ids),
        confidence="medium",
        flexibility=scheduled.flexibility,
        minimum_allowed_amount=scheduled.minimum_allowed_amount,
        source_event_ids=source_event_ids,
    )


def build_all_financial_states(
    store: DataStore, converter: CurrencyConverter, user_ids: Optional[List[str]] = None
) -> Dict[str, FinancialState]:
    """Build FinancialState for every requested user (default: everyone
    with a profile), reusing the same DataStore/CurrencyConverter so each
    user's events are indexed-looked-up exactly once."""
    ids = user_ids if user_ids is not None else sorted(store.profiles_by_user_id)
    return {uid: build_financial_state(store, converter, uid) for uid in ids}
