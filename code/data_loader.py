"""Deterministic, typed, indexed access to the Buy or Wait? dataset.

This module owns every CSV read in the project. Later phases (financial
state reconstruction, forecasting, payment-plan generation, ...) must go
through the ``DataStore`` API here rather than parsing ``dataset/*.csv``
themselves.

Design constraints (see implementation/phase1.md):

* No forecasting, recurrence detection, or affordability logic lives here.
* Blank ``financial_events.amount`` values stay ``None`` -- they are never
  coerced to zero, and this module never resolves them from images.
* Monetary amounts are parsed as :class:`decimal.Decimal`, never ``float``.
* Dataset paths are resolved relative to this file's location, not the
  current working directory.
* Schema and integrity problems raise a clear, typed exception rather than
  being silently patched over.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_CURRENCIES = frozenset({"INR", "ZAR", "IDR", "USD", "EUR"})


def default_dataset_dir() -> Path:
    """The repository's ``dataset/`` directory, resolved from this file's
    location so it is correct regardless of the process's working directory.
    """
    return Path(__file__).resolve().parent.parent / "dataset"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DataError(Exception):
    """Base class for every dataset loading/validation failure."""


class SchemaValidationError(DataError):
    """The actual CSV columns do not match the documented schema."""


class DataIntegrityError(DataError):
    """Cross-file integrity checks failed (see ``IntegrityReport``)."""


# ---------------------------------------------------------------------------
# Scalar parsing helpers
# ---------------------------------------------------------------------------


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def parse_str(value: Optional[str]) -> str:
    v = _clean(value)
    if v is None:
        raise ValueError("expected a non-empty string, got blank")
    return v


def parse_optional_str(value: Optional[str]) -> Optional[str]:
    return _clean(value)


def parse_bool(value: Optional[str]) -> bool:
    v = _clean(value)
    if v is None:
        raise ValueError("expected a boolean value, got blank")
    lowered = v.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise ValueError(f"unrecognized boolean value: {value!r}")


def parse_date(value: Optional[str]) -> date:
    v = _clean(value)
    if v is None:
        raise ValueError("expected a date (YYYY-MM-DD), got blank")
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid date {value!r}: {exc}") from exc


def parse_optional_date(value: Optional[str]) -> Optional[date]:
    v = _clean(value)
    if v is None:
        return None
    return parse_date(v)


def parse_datetime_utc(value: Optional[str]) -> datetime:
    v = _clean(value)
    if v is None:
        raise ValueError("expected an ISO-8601 datetime, got blank")
    iso = v[:-1] + "+00:00" if v.endswith("Z") else v
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise ValueError(f"invalid datetime {value!r}: {exc}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_decimal(value: Optional[str]) -> Decimal:
    v = _clean(value)
    if v is None:
        raise ValueError("expected a numeric amount, got blank")
    try:
        return Decimal(v)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc


def parse_optional_decimal(value: Optional[str]) -> Optional[Decimal]:
    v = _clean(value)
    if v is None:
        return None
    return parse_decimal(v)


def parse_int(value: Optional[str]) -> int:
    v = _clean(value)
    if v is None:
        raise ValueError("expected an integer, got blank")
    try:
        return int(v)
    except ValueError as exc:
        raise ValueError(f"invalid integer value: {value!r}") from exc


def parse_optional_int(value: Optional[str]) -> Optional[int]:
    v = _clean(value)
    if v is None:
        return None
    return parse_int(v)


def parse_pipe_list(value: Optional[str]) -> List[str]:
    v = _clean(value)
    if v is None:
        return []
    return [item.strip() for item in v.split("|") if item.strip()]


# ---------------------------------------------------------------------------
# Typed records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str


@dataclass(frozen=True)
class SampleRequest(Request):
    """A ``Request`` plus the solved output columns from sample_requests.csv."""

    amount_safe_to_pay: Decimal
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: Optional[date]
    spending_changes_needed: str
    decision_explanation: str


@dataclass(frozen=True)
class FinancialProfile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: List[str]
    expense_categories_to_protect: List[str]
    expense_categories_user_is_willing_to_reduce: List[str]
    expense_categories_user_is_willing_to_stop: List[str]
    payment_methods_user_will_consider: List[str]
    max_installment_months: Optional[int]


@dataclass(frozen=True)
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    amount: Optional[Decimal]  # None means genuinely missing -- never zero
    currency: str
    event_date: date
    settlement_date: Optional[date]
    status: str
    linked_event_id: Optional[str]
    flexibility: str
    minimum_allowed_amount: Optional[Decimal]


@dataclass(frozen=True)
class ExchangeRate:
    rate_date: date
    from_currency: str
    to_currency: str
    rate: Decimal


@dataclass(frozen=True)
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: Optional[int]
    financing_fee: Decimal
    total_payable_amount: Decimal


@dataclass(frozen=True)
class Message:
    message_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]
    sent_at: datetime
    source_type: str
    message_text: str


@dataclass(frozen=True)
class ImageRecord:
    image_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]
    image_path: Path


# ---------------------------------------------------------------------------
# CSV reading + schema validation
# ---------------------------------------------------------------------------

REQUEST_COLUMNS = [
    "request_id", "user_id", "request_date", "request_type",
    "requested_amount", "desired_completion_date", "allows_partial_payment",
    "request_text",
]

SAMPLE_REQUEST_COLUMNS = REQUEST_COLUMNS + [
    "amount_safe_to_pay", "affordability_status", "recommended_payment_method",
    "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed",
    "decision_explanation",
]

PROFILE_COLUMNS = [
    "user_id", "home_currency", "current_available_balance",
    "minimum_balance_to_keep", "financial_priorities",
    "expense_categories_to_protect", "expense_categories_user_is_willing_to_reduce",
    "expense_categories_user_is_willing_to_stop", "payment_methods_user_will_consider",
    "max_installment_months",
]

EVENT_COLUMNS = [
    "event_id", "user_id", "event_type", "description", "category", "direction",
    "amount", "currency", "event_date", "settlement_date", "status",
    "linked_event_id", "flexibility", "minimum_allowed_amount",
]

EXCHANGE_RATE_COLUMNS = ["rate_date", "from_currency", "to_currency", "rate"]

PAYMENT_OPTION_COLUMNS = [
    "payment_option_id", "request_id", "payment_method", "payment_amount",
    "number_of_payments", "first_payment_date", "payment_frequency_days",
    "financing_fee", "total_payable_amount",
]

MESSAGE_COLUMNS = [
    "message_id", "user_id", "request_id", "related_event_id", "sent_at",
    "source_type", "message_text",
]

IMAGE_COLUMNS = ["image_id", "user_id", "request_id", "related_event_id"]


def _read_csv_rows(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    if not path.exists():
        raise DataError(f"Required dataset file not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def _validate_columns(
    path: Path, fieldnames: List[str], expected: List[str], warnings: List[str]
) -> None:
    missing = [c for c in expected if c not in fieldnames]
    if missing:
        raise SchemaValidationError(
            f"{path.name}: missing expected column(s) {missing}; "
            f"found columns {fieldnames}. Refusing to guess -- update the "
            f"loader once the real schema is confirmed."
        )
    extra = [c for c in fieldnames if c not in expected]
    if extra:
        warnings.append(f"{path.name}: unexpected extra column(s) {extra} (ignored)")


# ---------------------------------------------------------------------------
# Per-file loaders
# ---------------------------------------------------------------------------


def _load_requests(path: Path, warnings: List[str]) -> List[Request]:
    fieldnames, rows = _read_csv_rows(path)
    _validate_columns(path, fieldnames, REQUEST_COLUMNS, warnings)
    out: List[Request] = []
    for line_no, row in enumerate(rows, start=2):
        try:
            out.append(Request(
                request_id=parse_str(row["request_id"]),
                user_id=parse_str(row["user_id"]),
                request_date=parse_date(row["request_date"]),
                request_type=parse_str(row["request_type"]),
                requested_amount=parse_decimal(row["requested_amount"]),
                desired_completion_date=parse_date(row["desired_completion_date"]),
                allows_partial_payment=parse_bool(row["allows_partial_payment"]),
                request_text=parse_str(row["request_text"]),
            ))
        except ValueError as exc:
            raise DataError(f"{path.name} line {line_no}: {exc}") from exc
    return out


def _load_sample_requests(path: Path, warnings: List[str]) -> List[SampleRequest]:
    fieldnames, rows = _read_csv_rows(path)
    _validate_columns(path, fieldnames, SAMPLE_REQUEST_COLUMNS, warnings)
    out: List[SampleRequest] = []
    for line_no, row in enumerate(rows, start=2):
        try:
            out.append(SampleRequest(
                request_id=parse_str(row["request_id"]),
                user_id=parse_str(row["user_id"]),
                request_date=parse_date(row["request_date"]),
                request_type=parse_str(row["request_type"]),
                requested_amount=parse_decimal(row["requested_amount"]),
                desired_completion_date=parse_date(row["desired_completion_date"]),
                allows_partial_payment=parse_bool(row["allows_partial_payment"]),
                request_text=parse_str(row["request_text"]),
                amount_safe_to_pay=parse_decimal(row["amount_safe_to_pay"]),
                affordability_status=parse_str(row["affordability_status"]),
                recommended_payment_method=parse_str(row["recommended_payment_method"]),
                payment_plan=parse_str(row["payment_plan"]),
                earliest_date_for_full_payment=parse_optional_date(row["earliest_date_for_full_payment"]),
                spending_changes_needed=parse_str(row["spending_changes_needed"]),
                decision_explanation=parse_str(row["decision_explanation"]),
            ))
        except ValueError as exc:
            raise DataError(f"{path.name} line {line_no}: {exc}") from exc
    return out


def _load_profiles(path: Path, warnings: List[str]) -> List[FinancialProfile]:
    fieldnames, rows = _read_csv_rows(path)
    _validate_columns(path, fieldnames, PROFILE_COLUMNS, warnings)
    out: List[FinancialProfile] = []
    for line_no, row in enumerate(rows, start=2):
        try:
            out.append(FinancialProfile(
                user_id=parse_str(row["user_id"]),
                home_currency=parse_str(row["home_currency"]),
                current_available_balance=parse_decimal(row["current_available_balance"]),
                minimum_balance_to_keep=parse_decimal(row["minimum_balance_to_keep"]),
                financial_priorities=parse_pipe_list(row["financial_priorities"]),
                expense_categories_to_protect=parse_pipe_list(row["expense_categories_to_protect"]),
                expense_categories_user_is_willing_to_reduce=parse_pipe_list(
                    row["expense_categories_user_is_willing_to_reduce"]),
                expense_categories_user_is_willing_to_stop=parse_pipe_list(
                    row["expense_categories_user_is_willing_to_stop"]),
                payment_methods_user_will_consider=parse_pipe_list(
                    row["payment_methods_user_will_consider"]),
                max_installment_months=parse_optional_int(row["max_installment_months"]),
            ))
        except ValueError as exc:
            raise DataError(f"{path.name} line {line_no}: {exc}") from exc
    return out


def _load_events(path: Path, warnings: List[str]) -> List[FinancialEvent]:
    fieldnames, rows = _read_csv_rows(path)
    _validate_columns(path, fieldnames, EVENT_COLUMNS, warnings)
    out: List[FinancialEvent] = []
    for line_no, row in enumerate(rows, start=2):
        try:
            out.append(FinancialEvent(
                event_id=parse_str(row["event_id"]),
                user_id=parse_str(row["user_id"]),
                event_type=parse_str(row["event_type"]),
                description=parse_str(row["description"]),
                category=parse_str(row["category"]),
                direction=parse_str(row["direction"]),
                amount=parse_optional_decimal(row["amount"]),  # blank stays None, never 0
                currency=parse_str(row["currency"]),
                event_date=parse_date(row["event_date"]),
                settlement_date=parse_optional_date(row["settlement_date"]),
                status=parse_str(row["status"]),
                linked_event_id=parse_optional_str(row["linked_event_id"]),
                flexibility=parse_str(row["flexibility"]),
                minimum_allowed_amount=parse_optional_decimal(row["minimum_allowed_amount"]),
            ))
        except ValueError as exc:
            raise DataError(f"{path.name} line {line_no}: {exc}") from exc
    return out


def _load_exchange_rates(path: Path, warnings: List[str]) -> List[ExchangeRate]:
    fieldnames, rows = _read_csv_rows(path)
    _validate_columns(path, fieldnames, EXCHANGE_RATE_COLUMNS, warnings)
    out: List[ExchangeRate] = []
    for line_no, row in enumerate(rows, start=2):
        try:
            out.append(ExchangeRate(
                rate_date=parse_date(row["rate_date"]),
                from_currency=parse_str(row["from_currency"]),
                to_currency=parse_str(row["to_currency"]),
                rate=parse_decimal(row["rate"]),
            ))
        except ValueError as exc:
            raise DataError(f"{path.name} line {line_no}: {exc}") from exc
    return out


def _load_payment_options(path: Path, warnings: List[str]) -> List[PaymentOption]:
    fieldnames, rows = _read_csv_rows(path)
    _validate_columns(path, fieldnames, PAYMENT_OPTION_COLUMNS, warnings)
    out: List[PaymentOption] = []
    for line_no, row in enumerate(rows, start=2):
        try:
            out.append(PaymentOption(
                payment_option_id=parse_str(row["payment_option_id"]),
                request_id=parse_str(row["request_id"]),
                payment_method=parse_str(row["payment_method"]),
                payment_amount=parse_decimal(row["payment_amount"]),
                number_of_payments=parse_int(row["number_of_payments"]),
                first_payment_date=parse_date(row["first_payment_date"]),
                payment_frequency_days=parse_optional_int(row["payment_frequency_days"]),
                financing_fee=parse_decimal(row["financing_fee"]),
                total_payable_amount=parse_decimal(row["total_payable_amount"]),
            ))
        except ValueError as exc:
            raise DataError(f"{path.name} line {line_no}: {exc}") from exc
    return out


def _load_messages(path: Path, warnings: List[str]) -> List[Message]:
    fieldnames, rows = _read_csv_rows(path)
    _validate_columns(path, fieldnames, MESSAGE_COLUMNS, warnings)
    out: List[Message] = []
    for line_no, row in enumerate(rows, start=2):
        try:
            out.append(Message(
                message_id=parse_str(row["message_id"]),
                user_id=parse_str(row["user_id"]),
                request_id=parse_optional_str(row["request_id"]),
                related_event_id=parse_optional_str(row["related_event_id"]),
                sent_at=parse_datetime_utc(row["sent_at"]),
                source_type=parse_str(row["source_type"]),
                message_text=parse_str(row["message_text"]),
            ))
        except ValueError as exc:
            raise DataError(f"{path.name} line {line_no}: {exc}") from exc
    return out


def _load_images(path: Path, dataset_dir: Path, warnings: List[str]) -> List[ImageRecord]:
    fieldnames, rows = _read_csv_rows(path)
    _validate_columns(path, fieldnames, IMAGE_COLUMNS, warnings)
    out: List[ImageRecord] = []
    for line_no, row in enumerate(rows, start=2):
        try:
            image_id = parse_str(row["image_id"])
            out.append(ImageRecord(
                image_id=image_id,
                user_id=parse_str(row["user_id"]),
                request_id=parse_optional_str(row["request_id"]),
                related_event_id=parse_optional_str(row["related_event_id"]),
                image_path=dataset_dir / "media" / "images" / f"{image_id}.png",
            ))
        except ValueError as exc:
            raise DataError(f"{path.name} line {line_no}: {exc}") from exc
    return out


# ---------------------------------------------------------------------------
# Indexing helpers
# ---------------------------------------------------------------------------


def _index_unique(items, key_fn, label: str, issues: List[str]) -> Dict:
    idx: Dict = {}
    for item in items:
        key = key_fn(item)
        if key in idx:
            issues.append(f"Duplicate {label}: {key!r}")
        else:
            idx[key] = item
    return idx


def _index_multi(items, key_fn) -> Dict[str, List]:
    idx: Dict[str, List] = defaultdict(list)
    for item in items:
        key = key_fn(item)
        if key is not None:
            idx[key].append(item)
    return dict(idx)


# ---------------------------------------------------------------------------
# Integrity report
# ---------------------------------------------------------------------------


@dataclass
class IntegrityReport:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# DataStore
# ---------------------------------------------------------------------------


class DataStore:
    """Typed, indexed, read-only view over the ``dataset/`` directory.

    Construct via :meth:`DataStore.load` rather than the constructor
    directly -- that is what performs loading, indexing, and integrity
    validation in the right order.
    """

    def __init__(self, dataset_dir: Path):
        self.dataset_dir = dataset_dir
        self.warnings: List[str] = []

        self.requests: List[Request] = []
        self.sample_requests: List[SampleRequest] = []
        self.profiles: List[FinancialProfile] = []
        self.events: List[FinancialEvent] = []
        self.exchange_rates: List[ExchangeRate] = []
        self.payment_options: List[PaymentOption] = []
        self.messages: List[Message] = []
        self.images: List[ImageRecord] = []

        self.integrity_report: IntegrityReport = IntegrityReport()

        # populated by _build_indexes()
        self._requests_by_id: Dict[str, Request] = {}
        self._sample_requests_by_id: Dict[str, SampleRequest] = {}
        self.profiles_by_user_id: Dict[str, FinancialProfile] = {}
        self.events_by_user_id: Dict[str, List[FinancialEvent]] = {}
        self.events_by_event_id: Dict[str, FinancialEvent] = {}
        self.messages_by_user_id: Dict[str, List[Message]] = {}
        self.messages_by_request_id: Dict[str, List[Message]] = {}
        self.messages_by_related_event_id: Dict[str, List[Message]] = {}
        self.images_by_image_id: Dict[str, ImageRecord] = {}
        self.images_by_user_id: Dict[str, List[ImageRecord]] = {}
        self.images_by_request_id: Dict[str, List[ImageRecord]] = {}
        self.images_by_related_event_id: Dict[str, List[ImageRecord]] = {}
        self.payment_options_by_request_id: Dict[str, List[PaymentOption]] = {}
        self.exchange_rates_by_date_and_currency_pair: Dict[Tuple[date, str, str], Decimal] = {}

        self._index_issues: List[str] = []

    # -- construction -------------------------------------------------

    @classmethod
    def load(cls, dataset_dir: Optional[Path] = None, *, strict: bool = True) -> "DataStore":
        """Load every dataset file, build indexes, and validate integrity.

        With ``strict=True`` (the default), a :class:`DataIntegrityError`
        is raised if any integrity check fails. Pass ``strict=False`` to
        get a store back regardless, with the failures recorded in
        ``store.integrity_report`` for the caller to inspect.
        """
        store = cls(Path(dataset_dir) if dataset_dir is not None else default_dataset_dir())
        if not store.dataset_dir.exists():
            raise DataError(f"dataset directory not found: {store.dataset_dir}")
        store._load_all()
        store._build_indexes()
        store.integrity_report = store.validate_integrity()
        if strict and not store.integrity_report.ok:
            raise DataIntegrityError(
                "Dataset integrity check failed:\n"
                + "\n".join(f"  - {e}" for e in store.integrity_report.errors)
            )
        return store

    def _load_all(self) -> None:
        d = self.dataset_dir
        self.requests = _load_requests(d / "requests.csv", self.warnings)
        self.sample_requests = _load_sample_requests(d / "sample_requests.csv", self.warnings)
        self.profiles = _load_profiles(d / "financial_profiles.csv", self.warnings)
        self.events = _load_events(d / "financial_events.csv", self.warnings)
        self.exchange_rates = _load_exchange_rates(d / "exchange_rates.csv", self.warnings)
        self.payment_options = _load_payment_options(d / "request_payment_options.csv", self.warnings)
        self.messages = _load_messages(d / "messages.csv", self.warnings)
        self.images = _load_images(d / "images.csv", d, self.warnings)

    def _build_indexes(self) -> None:
        issues = self._index_issues

        self._requests_by_id = _index_unique(self.requests, lambda r: r.request_id, "request_id", issues)
        self._sample_requests_by_id = _index_unique(
            self.sample_requests, lambda r: r.request_id, "sample request_id", issues)

        overlap = set(self._requests_by_id) & set(self._sample_requests_by_id)
        if overlap:
            issues.append(
                f"request_id(s) appear in both requests.csv and sample_requests.csv: {sorted(overlap)}"
            )

        self.profiles_by_user_id = _index_unique(self.profiles, lambda p: p.user_id, "profile user_id", issues)

        self.events_by_event_id = _index_unique(self.events, lambda e: e.event_id, "event_id", issues)
        self.events_by_user_id = _index_multi(self.events, lambda e: e.user_id)

        self.payment_options_by_request_id = _index_multi(self.payment_options, lambda p: p.request_id)
        _index_unique(self.payment_options, lambda p: p.payment_option_id, "payment_option_id", issues)

        self.messages_by_user_id = _index_multi(self.messages, lambda m: m.user_id)
        self.messages_by_request_id = _index_multi(self.messages, lambda m: m.request_id)
        self.messages_by_related_event_id = _index_multi(self.messages, lambda m: m.related_event_id)
        _index_unique(self.messages, lambda m: m.message_id, "message_id", issues)

        self.images_by_image_id = _index_unique(self.images, lambda i: i.image_id, "image_id", issues)
        self.images_by_user_id = _index_multi(self.images, lambda i: i.user_id)
        self.images_by_request_id = _index_multi(self.images, lambda i: i.request_id)
        self.images_by_related_event_id = _index_multi(self.images, lambda i: i.related_event_id)

        self.exchange_rates_by_date_and_currency_pair = {}
        for rate in self.exchange_rates:
            key = (rate.rate_date, rate.from_currency, rate.to_currency)
            if key in self.exchange_rates_by_date_and_currency_pair:
                issues.append(f"Duplicate exchange rate row for {key}")
            else:
                self.exchange_rates_by_date_and_currency_pair[key] = rate.rate

    # -- integrity ------------------------------------------------------

    def validate_integrity(self) -> IntegrityReport:
        """Run every cross-file integrity check described in
        implementation/phase1.md and return a full report (does not raise).
        """
        report = IntegrityReport()
        report.errors.extend(self._index_issues)

        all_requests: List[Request] = list(self.requests) + list(self.sample_requests)

        # Every request has exactly one matching profile.
        for r in all_requests:
            if r.user_id not in self.profiles_by_user_id:
                report.errors.append(
                    f"{r.request_id}: no financial profile found for user {r.user_id!r}")

        # Every request has at least one payment option.
        for r in all_requests:
            if not self.payment_options_by_request_id.get(r.request_id):
                report.errors.append(f"{r.request_id}: no payment options found")

        # Every payment option belongs to a real request.
        known_request_ids = set(self._requests_by_id) | set(self._sample_requests_by_id)
        for po in self.payment_options:
            if po.request_id not in known_request_ids:
                report.errors.append(
                    f"{po.payment_option_id}: request_id {po.request_id!r} does not exist")

        # Every referenced image file exists on disk.
        for img in self.images:
            if not img.image_path.exists():
                report.errors.append(
                    f"{img.image_id}: expected image file not found at {img.image_path}")

        # Every blank-amount event has a matching image record.
        for ev in self.events:
            if ev.amount is None and not self.images_by_related_event_id.get(ev.event_id):
                report.errors.append(
                    f"{ev.event_id}: amount is blank but no images.csv row references it")

        # No image points to a nonexistent event.
        for img in self.images:
            if img.related_event_id and img.related_event_id not in self.events_by_event_id:
                report.errors.append(
                    f"{img.image_id}: related_event_id {img.related_event_id!r} "
                    f"does not exist in financial_events.csv")

        # Messages should not dangle either (soft check -- not in the
        # explicit checklist, but cheap and useful to surface).
        for m in self.messages:
            if m.related_event_id and m.related_event_id not in self.events_by_event_id:
                report.warnings.append(
                    f"{m.message_id}: related_event_id {m.related_event_id!r} does not exist")
            if m.request_id and m.request_id not in known_request_ids:
                report.warnings.append(
                    f"{m.message_id}: request_id {m.request_id!r} does not exist")

        # Exchange-rate rows: positive rate, supported currencies.
        for rate in self.exchange_rates:
            if rate.rate <= 0:
                report.errors.append(
                    f"exchange rate {rate.rate_date} {rate.from_currency}->{rate.to_currency}: "
                    f"non-positive rate {rate.rate}")
            if rate.from_currency not in SUPPORTED_CURRENCIES:
                report.errors.append(
                    f"exchange rate {rate.rate_date}: unsupported from_currency {rate.from_currency!r}")
            if rate.to_currency not in SUPPORTED_CURRENCIES:
                report.errors.append(
                    f"exchange rate {rate.rate_date}: unsupported to_currency {rate.to_currency!r}")

        # Every profile uses a supported home currency.
        for p in self.profiles:
            if p.home_currency not in SUPPORTED_CURRENCIES:
                report.errors.append(f"{p.user_id}: unsupported home_currency {p.home_currency!r}")

        # Every event uses a supported currency.
        for ev in self.events:
            if ev.currency not in SUPPORTED_CURRENCIES:
                report.errors.append(f"{ev.event_id}: unsupported currency {ev.currency!r}")

        return report

    # -- observed-vs-expected counts (informational only) ---------------

    #: Counts observed during Phase 0 reconnaissance. Used only to produce
    #: an informational diff if the dataset changes size later -- never as
    #: a validation gate (a differently-sized dataset is not necessarily
    #: wrong).
    PHASE0_OBSERVED_COUNTS = {
        "requests": 250,
        "sample_requests": 25,
        "profiles": 275,
        "events": 25342,
        "exchange_rates": 134,
        "payment_options": 790,
        "messages": 215,
        "images": 16,
    }

    def summary(self) -> Dict[str, int]:
        return {
            "requests": len(self.requests),
            "sample_requests": len(self.sample_requests),
            "profiles": len(self.profiles),
            "events": len(self.events),
            "exchange_rates": len(self.exchange_rates),
            "payment_options": len(self.payment_options),
            "messages": len(self.messages),
            "images": len(self.images),
        }

    def count_drift_notes(self) -> List[str]:
        """Informational only -- differences from the Phase 0 counts are
        not errors, just worth a note in case the dataset was updated."""
        notes = []
        observed = self.summary()
        for key, expected in self.PHASE0_OBSERVED_COUNTS.items():
            actual = observed[key]
            if actual != expected:
                notes.append(f"{key}: Phase 0 saw {expected}, now {actual}")
        return notes

    # -- access API -------------------------------------------------------

    def get_request(self, request_id: str) -> Optional[Request]:
        return self._requests_by_id.get(request_id)

    def get_sample_request(self, request_id: str) -> Optional[SampleRequest]:
        return self._sample_requests_by_id.get(request_id)

    def get_profile(self, user_id: str) -> Optional[FinancialProfile]:
        return self.profiles_by_user_id.get(user_id)

    def get_events_for_user(self, user_id: str) -> List[FinancialEvent]:
        return list(self.events_by_user_id.get(user_id, []))

    def get_event(self, event_id: str) -> Optional[FinancialEvent]:
        return self.events_by_event_id.get(event_id)

    def get_payment_options(self, request_id: str) -> List[PaymentOption]:
        return list(self.payment_options_by_request_id.get(request_id, []))

    def get_messages_for_user(self, user_id: str) -> List[Message]:
        return list(self.messages_by_user_id.get(user_id, []))

    def get_messages_for_request(self, request_id: str) -> List[Message]:
        return list(self.messages_by_request_id.get(request_id, []))

    def get_messages_for_event(self, event_id: str) -> List[Message]:
        return list(self.messages_by_related_event_id.get(event_id, []))

    def get_image(self, image_id: str) -> Optional[ImageRecord]:
        return self.images_by_image_id.get(image_id)

    def get_images_for_event(self, event_id: str) -> List[ImageRecord]:
        return list(self.images_by_related_event_id.get(event_id, []))

    def get_images_for_request(self, request_id: str) -> List[ImageRecord]:
        return list(self.images_by_request_id.get(request_id, []))

    def get_images_for_user(self, user_id: str) -> List[ImageRecord]:
        return list(self.images_by_user_id.get(user_id, []))
