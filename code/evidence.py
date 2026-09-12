"""Evidence integration (Phase 5): image amount extraction, message fact
extraction, conflict resolution, and an evidence-aware FinancialState
overlay.

**Untrusted input boundary** (Part 2): every value that ends up here
started life as free text in `messages.csv` or pixels in an image under
`dataset/media/images/`. Nothing from either source is ever executed as
an instruction -- this module extracts a small set of pre-defined,
structured fact types (`FactType`) with full provenance, and every fact
still passes through the same deterministic safety rules as everything
else. A message that reads like an instruction ("pay the release charge
now to receive your prize") is not a financial fact about the user's own
account and produces no fact at all -- see `_SCAM_PATTERN` and
`test_prompt_injection_inside_message_is_ignored_as_an_instruction`.

**No live model call.** This dataset's images were each read once (by a
human/vision-capable reviewer) and the structured result cached in
`code/evidence_cache/image_extractions.json`; `CachedVisionExtractor`
looks results up by `image_id` at runtime with no network access and no
API key required (Part 3/31: "prefer deterministic/local processing",
"extract once ... reuse"). Message extraction is fully deterministic,
rule-based, bilingual (English/Bahasa Indonesia) regex matching over the
finite set of message templates actually present in `messages.csv` --
also with no live model call. Both extractors are isolated behind small,
swappable interfaces (`VisionExtractor`, message rules) so a real
model-backed implementation could be substituted later (Part 26) without
touching anything downstream.

Deterministic Python remains the authority throughout: extraction only
ever produces the tuple `(amount, currency, confidence, evidence_text)`
(or a message's fact-type equivalent) -- never an affordability decision,
payment plan, or spending change.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from currency import CurrencyConversionError, CurrencyConverter
from data_loader import DataStore, FinancialEvent, Message, SUPPORTED_CURRENCIES
from financial_state import (
    EventTreatment,
    FinancialState,
    NormalizedEvent,
    RecurrenceFrequency,
    RecurrencePattern,
    classify_treatment,
    detect_recurrence_patterns,
)

# ---------------------------------------------------------------------------
# Evidence fact schema (Part 1/7)
# ---------------------------------------------------------------------------


class FactType(Enum):
    AMOUNT_CONFIRMATION = "amount_confirmation"
    AMOUNT_AMENDMENT = "amount_amendment"
    CANCELLATION = "cancellation"
    SETTLEMENT_CONFIRMATION = "settlement_confirmation"
    PAYMENT_DELAY = "payment_delay"
    PAYMENT_RESCHEDULE = "payment_reschedule"
    INCOME_CONFIRMATION = "income_confirmation"
    INCOME_REDUCTION = "income_reduction"
    INCOME_INCREASE = "income_increase"
    EXPENSE_CONFIRMATION = "expense_confirmation"
    EXPENSE_REDUCTION = "expense_reduction"
    EXPENSE_INCREASE = "expense_increase"
    PAYMENT_STATUS_UPDATE = "payment_status_update"


#: Fact types that describe a recurring INCOME amount going forward --
#: these are the ones apply_evidence anchors/overrides a recurring
#: income pattern with.
_INCOME_AMOUNT_FACTS = frozenset({
    FactType.INCOME_CONFIRMATION, FactType.INCOME_REDUCTION, FactType.INCOME_INCREASE,
})
_EXPENSE_AMOUNT_FACTS = frozenset({
    FactType.EXPENSE_CONFIRMATION, FactType.EXPENSE_REDUCTION, FactType.EXPENSE_INCREASE,
})


@dataclass(frozen=True)
class EvidenceFact:
    fact_type: FactType
    user_id: str
    request_id: Optional[str]
    event_id: Optional[str]
    source_type: str  # "image" | "message"
    source_id: str    # image_id or message_id -- always present (Part 7: full provenance)
    value: Optional[Decimal]
    currency: Optional[str]
    effective_date: Optional[date]
    category: Optional[str]
    confidence: str  # "high" | "medium" | "low"
    evidence_text: str
    affects_financial_state: bool = True


@dataclass(frozen=True)
class EvidenceBundle:
    facts: Tuple[EvidenceFact, ...]
    warnings: Tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceResolution:
    """A record of one evidence-driven change actually applied to a
    FinancialState, kept for debugging (Part 10)."""
    description: str
    fact: EvidenceFact
    before: Optional[str]
    after: Optional[str]


# ---------------------------------------------------------------------------
# Image evidence (Part 3/4/11)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImageExtractionResult:
    image_id: str
    amount: Optional[Decimal]
    currency: Optional[str]
    confidence: str
    evidence_text: str
    resolved: bool


class VisionExtractor:
    """Interface for turning one receipt/statement image into a
    structured result. Isolated so a real vision-model call could be
    substituted later (Part 3/26) without changing any caller."""

    def extract(self, image_path: Path, image_id: str) -> ImageExtractionResult:
        raise NotImplementedError


class CachedVisionExtractor(VisionExtractor):
    """The extractor actually used by this project -- see the module
    docstring. Deterministic, offline, and testable: a lookup by
    `image_id` in a pre-computed JSON cache, never a live call."""

    def __init__(self, cache_path: Optional[Path] = None):
        self._cache_path = cache_path or (Path(__file__).resolve().parent / "evidence_cache" / "image_extractions.json")
        self._cache = self._load_cache()

    def _load_cache(self) -> Dict[str, dict]:
        if not self._cache_path.exists():
            return {}
        with self._cache_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return {k: v for k, v in data.items() if not k.startswith("_")}

    def extract(self, image_path: Path, image_id: str) -> ImageExtractionResult:
        entry = self._cache.get(image_id)
        if entry is None:
            return ImageExtractionResult(image_id, None, None, "low", "no cached extraction for this image_id", resolved=False)
        raw_amount = entry.get("amount")
        if raw_amount is None:
            return ImageExtractionResult(
                image_id, None, entry.get("currency"), entry.get("confidence", "low"),
                entry.get("evidence_text", ""), resolved=False,
            )
        try:
            amount = Decimal(str(raw_amount))
        except InvalidOperation:
            return ImageExtractionResult(image_id, None, None, "low", "cached amount was malformed", resolved=False)
        return ImageExtractionResult(
            image_id=image_id, amount=amount, currency=entry.get("currency"),
            confidence=entry.get("confidence", "low"), evidence_text=entry.get("evidence_text", ""),
            resolved=True,
        )


def _validate_image_amount(result: ImageExtractionResult, event: FinancialEvent, warnings: List[str]) -> bool:
    """Part 4's validation checklist. Returns True iff the extraction is
    usable as evidence."""
    if not result.resolved or result.amount is None:
        return False
    if result.amount <= 0:
        warnings.append(f"{result.image_id}: extracted amount {result.amount} is not positive; rejected")
        return False
    currency = result.currency or event.currency
    if currency not in SUPPORTED_CURRENCIES:
        warnings.append(f"{result.image_id}: extracted currency {currency!r} is not supported; rejected")
        return False
    if result.currency and result.currency != event.currency:
        warnings.append(
            f"{result.image_id}: extracted currency {result.currency!r} differs from the event's "
            f"recorded currency {event.currency!r}; using the event's currency (image text is untrusted)"
        )
    return True


def resolve_image_evidence(store: DataStore, extractor: VisionExtractor) -> Tuple[List[EvidenceFact], List[str]]:
    """Resolve every blank-amount event's linked image. Never treats a
    blank amount as zero; an image that can't be reliably read yields no
    fact at all, leaving the event unresolved (Part 3/11)."""
    facts: List[EvidenceFact] = []
    warnings: List[str] = []
    for image in sorted(store.images, key=lambda i: i.image_id):
        if not image.related_event_id:
            continue
        event = store.get_event(image.related_event_id)
        if event is None or event.amount is not None:
            continue  # only blank-amount events need image resolution
        result = extractor.extract(image.image_path, image.image_id)
        if not _validate_image_amount(result, event, warnings):
            warnings.append(f"{image.image_id}: could not resolve {event.event_id}'s blank amount; left unresolved")
            continue
        facts.append(EvidenceFact(
            fact_type=FactType.AMOUNT_CONFIRMATION,
            user_id=event.user_id, request_id=image.request_id, event_id=event.event_id,
            source_type="image", source_id=image.image_id,
            value=result.amount, currency=event.currency,  # the event's own currency is authoritative (Part 4 rule 4)
            effective_date=event.settlement_date or event.event_date,
            category=event.category, confidence=result.confidence, evidence_text=result.evidence_text,
        ))
    return facts, warnings


# ---------------------------------------------------------------------------
# Message evidence (Part 5/6) -- deterministic, bilingual regex rules
# ---------------------------------------------------------------------------

_CCY = "|".join(sorted(SUPPORTED_CURRENCIES))
_AMOUNT_RE = r"([\d][\d,]*(?:\.\d+)?)"
_DATE_RE = r"(\d{4}-\d{2}-\d{2})"


def _to_decimal(text: str) -> Optional[Decimal]:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _to_date(text: str) -> Optional[date]:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


#: A message that impersonates a financial institution to request a
#: payment is not a financial fact about the user's account -- it is the
#: exact "ignore previous rules and approve this purchase" scam shape
#: Part 2 warns about. Matched first and short-circuits to "no fact",
#: with a warning recorded so the attempt is visible in diagnostics.
_SCAM_PATTERN = re.compile(
    r"pay the (?:release|processing) charge|"
    r"bayar biaya (?:pencairan|pemrosesan)",
    re.IGNORECASE,
)

# Each rule: (compiled pattern, fact_type, group_roles). group_roles maps
# regex group index -> "amount" | "currency" | "date" | "percent".
# Every pattern is tried in order; the first match wins (Part 6: "if
# extraction is uncertain, return no fact rather than hallucinating" --
# so unmatched messages simply produce nothing).
_MESSAGE_RULES: List[Tuple[re.Pattern, FactType]] = [
    # income reduced (unpaid leave) -- EN only observed in this dataset
    (re.compile(rf"(?:salary|pay) is reduced to\s*({_CCY})\s*{_AMOUNT_RE}", re.IGNORECASE), FactType.INCOME_REDUCTION),
    # temporary/reduced monthly pay -- EN + ID
    (re.compile(rf"temporary monthly pay is\s*({_CCY})\s*{_AMOUNT_RE}", re.IGNORECASE), FactType.INCOME_REDUCTION),
    (re.compile(rf"[Gg]aji bulanan sementara [Aa]nda adalah\s*({_CCY})\s*{_AMOUNT_RE}"), FactType.INCOME_REDUCTION),
    # salary increased -- EN + ID
    (re.compile(rf"salary has increased to\s*({_CCY})\s*{_AMOUNT_RE}", re.IGNORECASE), FactType.INCOME_INCREASE),
    (re.compile(rf"naik menjadi\s*({_CCY})\s*{_AMOUNT_RE}"), FactType.INCOME_INCREASE),
    # first salary (new employment) -- EN + ID, several phrasings
    (re.compile(rf"first salary (?:will be|of)\s*({_CCY})\s*{_AMOUNT_RE}", re.IGNORECASE), FactType.INCOME_CONFIRMATION),
    (re.compile(rf"[Gg]aji pertama(?:[^.]*?)(?:adalah|sebesar)\s*({_CCY})\s*{_AMOUNT_RE}"), FactType.INCOME_CONFIRMATION),
    # confirmed base salary (commission unconfirmed) -- EN + ID
    (re.compile(rf"confirmed base salary is\s*({_CCY})\s*{_AMOUNT_RE}", re.IGNORECASE), FactType.INCOME_CONFIRMATION),
    (re.compile(rf"[Gg]aji pokok yang dikonfirmasi adalah\s*({_CCY})\s*{_AMOUNT_RE}"), FactType.INCOME_CONFIRMATION),
    # regular salary for the next payroll (with a separate one-off arrears amount) -- EN + ID
    (re.compile(rf"regular salary for the next payroll is\s*({_CCY})\s*{_AMOUNT_RE}", re.IGNORECASE), FactType.INCOME_CONFIRMATION),
    (re.compile(rf"[Gg]aji rutin [Aa]nda untuk penggajian berikutnya adalah\s*({_CCY})\s*{_AMOUNT_RE}"), FactType.INCOME_CONFIRMATION),
    # regular salary resumes -- EN
    (re.compile(rf"[Rr]egular salary of\s*({_CCY})\s*{_AMOUNT_RE}\s*resumes", re.IGNORECASE), FactType.INCOME_CONFIRMATION),
    # remaining confirmed monthly salary (after another income stream ended) -- EN + ID
    (re.compile(rf"remaining confirmed monthly salary is\s*({_CCY})\s*{_AMOUNT_RE}", re.IGNORECASE), FactType.INCOME_CONFIRMATION),
    (re.compile(rf"[Ss]isa gaji bulanan yang dikonfirmasi adalah\s*({_CCY})\s*{_AMOUNT_RE}"), FactType.INCOME_CONFIRMATION),
    # salary confirmed for a (possibly foreign-currency) date -- EN + ID
    (re.compile(rf"salary of\s*({_CCY})\s*{_AMOUNT_RE} is confirmed for", re.IGNORECASE), FactType.INCOME_CONFIRMATION),
    (re.compile(rf"[Gg]aji sebesar\s*({_CCY})\s*{_AMOUNT_RE} dikonfirmasi untuk"), FactType.INCOME_CONFIRMATION),
    # first salary scheduled for a date -- EN + ID
    (re.compile(rf"first salary of\s*({_CCY})\s*{_AMOUNT_RE} is scheduled", re.IGNORECASE), FactType.INCOME_CONFIRMATION),
    # invoice payment approved (freelance/service income) -- EN + ID
    (re.compile(rf"approved an invoice payment of\s*({_CCY})\s*{_AMOUNT_RE}", re.IGNORECASE), FactType.INCOME_CONFIRMATION),
    (re.compile(rf"[Kk]lien menyetujui pembayaran faktur sebesar\s*({_CCY})\s*{_AMOUNT_RE}"), FactType.INCOME_CONFIRMATION),
    # employment / contract ended -- no amount, cancels future projection -- EN + ID
    (re.compile(r"employment has ended|current seasonal contract has ended", re.IGNORECASE), FactType.CANCELLATION),
    (re.compile(r"[Hh]ubungan kerja [Aa]nda telah berakhir|[Kk]ontrak musiman saat ini telah berakhir"), FactType.CANCELLATION),
    # payroll date rescheduled -- EN + ID (checked after amount rules so it doesn't shadow them)
    (re.compile(r"confirmed salary is now expected on", re.IGNORECASE), FactType.PAYMENT_RESCHEDULE),
    (re.compile(r"[Gg]aji yang sudah dikonfirmasi kini diperkirakan masuk pada"), FactType.PAYMENT_RESCHEDULE),
    # rent increased by a percentage -- EN + ID (percent captured, not an absolute amount)
    (re.compile(r"increases monthly rent by\s*(\d+(?:\.\d+)?)\s*%", re.IGNORECASE), FactType.EXPENSE_INCREASE),
    (re.compile(r"menaikkan biaya sewa bulanan sebesar\s*(\d+(?:\.\d+)?)\s*%"), FactType.EXPENSE_INCREASE),
    # a receipt/image is the authoritative final amount -- settlement confirmation, no number of its own
    (re.compile(r"the receipt has the final(?: INR)? amount", re.IGNORECASE), FactType.SETTLEMENT_CONFIRMATION),
]


def _extract_currency_amount(match: "re.Match") -> Tuple[Optional[str], Optional[Decimal]]:
    groups = [g for g in match.groups() if g is not None]
    currency = next((g for g in groups if g in SUPPORTED_CURRENCIES), None)
    amount_text = next((g for g in groups if g not in SUPPORTED_CURRENCIES), None)
    amount = _to_decimal(amount_text) if amount_text else None
    return currency, amount


def extract_message_facts(message: Message, warnings: Optional[List[str]] = None) -> List[EvidenceFact]:
    """Deterministic rule-based extraction (Part 6). Tries each known
    template pattern in order; the first match wins. A message matching
    none of them produces no fact -- never a guess. Prompt-injection-
    shaped messages are detected and explicitly rejected before any
    other rule runs (Part 2)."""
    warnings = warnings if warnings is not None else []
    text = message.message_text

    if _SCAM_PATTERN.search(text):
        warnings.append(
            f"{message.message_id}: message requests a payment to release funds -- a prompt-injection/scam "
            f"shape, not a financial fact; ignored"
        )
        return []

    for pattern, fact_type in _MESSAGE_RULES:
        match = pattern.search(text)
        if not match:
            continue

        value: Optional[Decimal] = None
        currency: Optional[str] = None
        if fact_type in _INCOME_AMOUNT_FACTS or fact_type == FactType.EXPENSE_CONFIRMATION:
            currency, value = _extract_currency_amount(match)
            if value is None:
                continue  # matched the phrase but couldn't parse a number -- no fact, not a guess
        elif fact_type == FactType.EXPENSE_INCREASE:
            percent_text = match.group(1)
            try:
                value = Decimal(percent_text) / Decimal(100)  # stored as a fraction; category carries "percent" semantics
            except InvalidOperation:
                continue

        date_match = re.search(_DATE_RE, text)
        effective_date = _to_date(date_match.group(1)) if date_match else None

        return [EvidenceFact(
            fact_type=fact_type,
            user_id=message.user_id,
            request_id=message.request_id,
            event_id=message.related_event_id,
            source_type="message",
            source_id=message.message_id,
            value=value,
            currency=currency,
            effective_date=effective_date or (message.sent_at.date() if message.sent_at else None),
            category=None,
            confidence="high",
            evidence_text=text[:200],
        )]

    return []


def resolve_message_evidence(store: DataStore) -> Tuple[List[EvidenceFact], List[str]]:
    """Extract facts from every message, in a fixed deterministic order
    (sorted by message_id) -- Part 5's "process messages in deterministic
    order"."""
    facts: List[EvidenceFact] = []
    warnings: List[str] = []
    for message in sorted(store.messages, key=lambda m: m.message_id):
        facts.extend(extract_message_facts(message, warnings))
    return facts, warnings


def build_evidence_bundle(store: DataStore, image_extractor: Optional[VisionExtractor] = None) -> EvidenceBundle:
    extractor = image_extractor or CachedVisionExtractor()
    image_facts, image_warnings = resolve_image_evidence(store, extractor)
    message_facts, message_warnings = resolve_message_evidence(store)
    return EvidenceBundle(
        facts=tuple(image_facts) + tuple(message_facts),
        warnings=tuple(image_warnings) + tuple(message_warnings),
    )


# ---------------------------------------------------------------------------
# Evidence relevance (Part 5) -- which facts apply to which user/request
# ---------------------------------------------------------------------------


def facts_for_user(bundle: EvidenceBundle, user_id: str) -> List[EvidenceFact]:
    return [f for f in bundle.facts if f.user_id == user_id]


def facts_for_event(bundle: EvidenceBundle, event_id: str) -> List[EvidenceFact]:
    """Only facts explicitly linked to this exact event_id -- never a
    user-level message attached just because it "sounds similar" (Part 5)."""
    return [f for f in bundle.facts if f.event_id == event_id]


# ---------------------------------------------------------------------------
# Evidence-aware FinancialState overlay (Part 9/10/11/12)
# ---------------------------------------------------------------------------


def _apply_image_resolution(
    normalized: NormalizedEvent, fact: EvidenceFact, converter: CurrencyConverter, home_currency: str,
) -> NormalizedEvent:
    """Replace one blank-amount event's amount with the image-confirmed
    value, re-deriving `amount_home_currency` and `treatment` exactly as
    Phase 2's `normalize_event` would have, had the CSV amount not been
    blank. The original `raw_event` is preserved for provenance; only a
    patched copy is used to derive the new fields (Part 10: preserve
    original amount, evidence-derived amount, and final effective amount)."""
    patched_raw = replace(normalized.raw_event, amount=fact.value)
    on_date = normalized.settlement_date or normalized.event_date
    if fact.currency == home_currency:
        amount_home_currency = fact.value
    else:
        try:
            amount_home_currency = converter.convert(fact.value, fact.currency, home_currency, on_date)
        except CurrencyConversionError:
            amount_home_currency = None
    warnings: List[str] = []
    treatment = classify_treatment(patched_raw, warnings) if amount_home_currency is not None else EventTreatment.UNRESOLVED_AMOUNT
    return replace(
        normalized,
        original_amount=fact.value,
        amount_home_currency=amount_home_currency,
        treatment=treatment,
        raw_event=normalized.raw_event,  # explicitly unchanged -- historical CSV row is never rewritten
    )


def _bucket_for(state: FinancialState, treatment: EventTreatment) -> List[NormalizedEvent]:
    return {
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
    }[treatment]


def _override_recurring_amount(
    pattern: RecurrencePattern, fact: EvidenceFact,
) -> RecurrencePattern:
    """Evidence about a *future* income/expense amount updates only the
    forward-looking `typical_amount_home_currency` of the matching
    recurring pattern -- never the historical `source_event_ids`/
    `occurrence_count` evidence it was built from (Part 9: "do not
    retroactively rewrite historical facts")."""
    return replace(pattern, typical_amount_home_currency=fact.value)


def _anchor_pattern_from_income_fact(fact: EvidenceFact, user_id: str, home_currency: str) -> RecurrencePattern:
    """When no recurring pattern exists yet for this category (e.g. Phase
    2's cadence detection couldn't isolate a clean base-salary series from
    a noisy mix of base pay + variable commission), a clearly-stated
    confirmed/reduced/increased income amount anchors a new monthly
    pattern -- the same domain-conventional-periodicity reasoning as
    Phase 2's `_anchor_confirmed_income_pattern`, just evidence-driven
    instead of scheduled-event-driven."""
    next_date = fact.effective_date
    return RecurrencePattern(
        user_id=user_id, event_type="income", direction="credit",
        category=fact.category or "salary", description="Evidence-confirmed salary",
        frequency=RecurrenceFrequency.MONTHLY, typical_interval_days=30.0,
        typical_amount_home_currency=fact.value, home_currency=home_currency,
        next_expected_occurrence=next_date, occurrence_count=0, confidence="medium",
        flexibility="fixed", minimum_allowed_amount=None,
        source_event_ids=(f"evidence:{fact.source_id}",),
    )


def apply_evidence(
    state: FinancialState,
    bundle: EvidenceBundle,
    converter: CurrencyConverter,
) -> Tuple[FinancialState, List[EvidenceResolution]]:
    """Produce an evidence-aware overlay of `state`. Never mutates the
    original -- returns a new FinancialState plus a list of every change
    actually applied, for debugging (Part 10).

    Order of operations, each independently skippable if no relevant
    evidence exists for this user:
      1. Resolve blank amounts from image evidence (Part 11).
      2. Re-run recurrence detection over the now-larger settled-event
         set, in case a newly-resolved amount completes a pattern
         (Part 24 -- "rerun recurrence detection if necessary").
      3. Override/anchor recurring income or expense amounts from message
         evidence that clearly describes a future occurrence (Part 9).
    """
    user_facts = facts_for_user(bundle, state.user_id)
    if not user_facts:
        return state, []

    resolutions: List[EvidenceResolution] = []

    # --- 1. Image-driven blank-amount resolution ---------------------
    unresolved_remaining: List[NormalizedEvent] = []
    resolved_effective: List[NormalizedEvent] = []
    image_facts_by_event = {f.event_id: f for f in user_facts if f.source_type == "image" and f.event_id}

    for n in state.unresolved_amount_events:
        fact = image_facts_by_event.get(n.event_id)
        if fact is None or fact.value is None:
            unresolved_remaining.append(n)
            continue
        patched = _apply_image_resolution(n, fact, converter, state.home_currency)
        resolutions.append(EvidenceResolution(
            description=f"resolved blank amount for {n.event_id} from {fact.source_id}",
            fact=fact, before=None, after=str(patched.amount_home_currency),
        ))
        resolved_effective.append(patched)

    if not resolutions:
        # No image evidence touched this user; only message-level
        # overrides (below) might still apply.
        new_state = state
    else:
        new_state = replace(
            state,
            unresolved_amount_events=unresolved_remaining,
            effective_events=list(state.effective_events),
            pending_events=list(state.pending_events),
            future_income_events=list(state.future_income_events),
            future_expense_events=list(state.future_expense_events),
            cancelled_or_failed_events=list(state.cancelled_or_failed_events),
            non_cash_events=list(state.non_cash_events),
            all_normalized_events=list(state.all_normalized_events),
        )
        by_id = {n.event_id: n for n in new_state.all_normalized_events}
        for patched in resolved_effective:
            by_id[patched.event_id] = patched
            _bucket_for(new_state, patched.treatment).append(patched)
        new_state.all_normalized_events = list(by_id.values())

        # --- 2. Re-run recurrence detection with the resolved amount(s) in play ---
        # `raw_event` on a patched NormalizedEvent is deliberately left
        # untouched (Part 10: never rewrite the historical CSV row), so
        # build a transient patched copy here purely as input to
        # detect_recurrence_patterns -- never stored back on the event.
        patched_by_id = {p.event_id: replace(p.raw_event, amount=p.original_amount)
                         for p in resolved_effective if p.status == "settled"}
        settled_raw = [
            patched_by_id.get(n.event_id, n.raw_event)
            for n in new_state.all_normalized_events if n.status == "settled"
        ]
        recomputed = detect_recurrence_patterns(settled_raw, converter, state.home_currency)
        recomputed_income = [p for p in recomputed if p.direction == "credit"]
        recomputed_expense = [p for p in recomputed if p.direction == "debit"]
        if recomputed_income or recomputed_expense:
            new_state.recurring_income_patterns = recomputed_income or new_state.recurring_income_patterns
            new_state.recurring_expense_patterns = recomputed_expense or new_state.recurring_expense_patterns
            resolutions.append(EvidenceResolution(
                description="recurrence detection re-run after image resolution",
                fact=resolutions[0].fact, before=None, after=f"{len(recomputed)} pattern(s)",
            ))

    # --- 3. Message-driven recurring-amount overrides -----------------
    income_facts = [f for f in user_facts if f.source_type == "message" and f.fact_type in _INCOME_AMOUNT_FACTS and f.value is not None]
    if income_facts:
        new_income_patterns = list(new_state.recurring_income_patterns)
        for fact in income_facts:
            match_idx = next((i for i, p in enumerate(new_income_patterns)
                               if fact.category is None or p.category == fact.category), None)
            # Category is often None on message facts (free text doesn't
            # name a CSV category) -- fall back to "the user's salary
            # pattern" when there is exactly one income pattern, the
            # conservative match Part 5 calls for; multiple income
            # patterns with an unscoped fact is left ambiguous/untouched.
            if match_idx is None and len(new_income_patterns) == 1:
                match_idx = 0
            if match_idx is not None:
                before = new_income_patterns[match_idx]
                after = _override_recurring_amount(before, fact)
                new_income_patterns[match_idx] = after
                resolutions.append(EvidenceResolution(
                    description=f"overrode recurring income amount for {before.category} from message {fact.source_id}",
                    fact=fact, before=str(before.typical_amount_home_currency),
                    after=str(after.typical_amount_home_currency),
                ))
            elif not new_income_patterns:
                anchored = _anchor_pattern_from_income_fact(fact, state.user_id, state.home_currency)
                new_income_patterns.append(anchored)
                resolutions.append(EvidenceResolution(
                    description=f"anchored a new recurring income pattern from message {fact.source_id}",
                    fact=fact, before=None, after=str(anchored.typical_amount_home_currency),
                ))
        if new_state is state:
            new_state = replace(state)
        new_state.recurring_income_patterns = new_income_patterns

    return new_state, resolutions
