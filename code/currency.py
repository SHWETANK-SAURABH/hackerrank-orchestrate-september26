"""Deterministic currency conversion using only ``dataset/exchange_rates.csv``.

No live rates, no market data, no interpolation, no "nearest date" fallback.
A conversion either resolves from an exact-date row in the dataset (direct
or inverted) or raises :class:`CurrencyConversionError` -- it never silently
guesses.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, Tuple

from data_loader import SUPPORTED_CURRENCIES, ExchangeRate


class CurrencyError(Exception):
    """Base class for currency-module failures."""


class UnsupportedCurrencyError(CurrencyError):
    """A currency outside {INR, ZAR, IDR, USD, EUR} was requested."""


class CurrencyConversionError(CurrencyError):
    """No exact-date direct or inverse rate exists for the requested pair."""


class CurrencyConverter:
    """Looks up and applies exchange rates from a fixed, pre-loaded table.

    Built from the ``ExchangeRate`` rows produced by
    :func:`data_loader.DataStore.load` -- it never reads a CSV itself.
    """

    def __init__(self, exchange_rates: Iterable[ExchangeRate]):
        self._rates: Dict[Tuple[date, str, str], Decimal] = {}
        for rate in exchange_rates:
            key = (rate.rate_date, rate.from_currency, rate.to_currency)
            # A duplicate row is a data-integrity concern handled by
            # DataStore.validate_integrity(); here we simply keep the
            # first one seen so this module never raises on load.
            self._rates.setdefault(key, rate.rate)

    @classmethod
    def from_data_store(cls, data_store) -> "CurrencyConverter":
        return cls(data_store.exchange_rates)

    def _check_supported(self, currency: str) -> None:
        if currency not in SUPPORTED_CURRENCIES:
            raise UnsupportedCurrencyError(
                f"unsupported currency {currency!r}; supported: {sorted(SUPPORTED_CURRENCIES)}"
            )

    def get_rate(self, rate_date: date, from_currency: str, to_currency: str) -> Decimal:
        """Return the exact-date rate to multiply an amount in
        ``from_currency`` by to get ``to_currency``.

        Tries, in order:
          1. a direct row for (rate_date, from_currency, to_currency)
          2. the inverse of a row for (rate_date, to_currency, from_currency)

        Raises ``CurrencyConversionError`` if neither exists. Never falls
        back to a nearby date or a different pair.
        """
        self._check_supported(from_currency)
        self._check_supported(to_currency)

        if from_currency == to_currency:
            return Decimal("1")

        direct = self._rates.get((rate_date, from_currency, to_currency))
        if direct is not None:
            return direct

        inverse = self._rates.get((rate_date, to_currency, from_currency))
        if inverse is not None:
            if inverse == 0:
                raise CurrencyConversionError(
                    f"inverse rate for {to_currency}->{from_currency} on {rate_date} is zero; "
                    f"cannot invert"
                )
            return Decimal("1") / inverse

        raise CurrencyConversionError(
            f"no exchange rate for {from_currency}->{to_currency} (or its inverse) "
            f"on exact date {rate_date}; live/nearby-date rates are not permitted"
        )

    def convert(
        self, amount: Decimal, from_currency: str, to_currency: str, on_date: date
    ) -> Decimal:
        """Convert ``amount`` (a Decimal) from one currency to another using
        the exact-date rate for ``on_date``. Returns ``amount`` unchanged
        (same object value, no rate lookup) when the currencies match.
        """
        if not isinstance(amount, Decimal):
            raise TypeError(f"amount must be a Decimal, got {type(amount).__name__}")
        if from_currency == to_currency:
            return amount
        rate = self.get_rate(on_date, from_currency, to_currency)
        return amount * rate
