"""Money amounts: computed with Decimal, stored as integer cents (1/100 of a unit).

Floating-point numbers are never used; the equality "balance = opening balance + movements" must hold exactly.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

CENT = Decimal("0.01")


def to_decimal(value) -> Decimal:
    """Converts text, an integer or a Decimal to a Decimal. A float is rejected."""
    if isinstance(value, float):
        raise TypeError("float para tutarı olarak kabul edilmez; metin ya da Decimal verin")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value).strip())


def to_cents(amount: Decimal) -> int:
    """Rounds a Decimal amount to the nearest cent (ties to even) and returns integer cents."""
    return int((amount.quantize(CENT, rounding=ROUND_HALF_EVEN) * 100).to_integral_value())


def from_cents(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(CENT)


def apply_rate(cents: int, rate: Decimal) -> int:
    """Applies a rate to an amount in cents and returns the result as integer cents again."""
    return to_cents(from_cents(cents) * rate)
