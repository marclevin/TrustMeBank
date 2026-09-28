"""Decimal-safe money helpers. This is the only module that turns strings into amounts."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

TWO_PLACES = Decimal("0.01")
MAX_AMOUNT = Decimal("1000000.00")
CURRENCY = "ZAR"


class InvalidAmount(ValueError):
    pass


def parse_amount(value: object, *, allow_zero: bool = False) -> Decimal:
    """Parse a user supplied amount. Accepts str, int or Decimal. Never accepts float."""
    if isinstance(value, bool) or isinstance(value, float):
        raise InvalidAmount('amount must be a string such as "500.00", not a float')
    if isinstance(value, Decimal):
        amount = value
    else:
        try:
            amount = Decimal(str(value).strip())
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise InvalidAmount("amount is not a valid decimal number") from exc
    if not amount.is_finite():
        raise InvalidAmount("amount must be a finite number")
    if amount.as_tuple().exponent < -2:
        raise InvalidAmount("amount may have at most two decimal places")
    amount = amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    if amount < 0 or (amount == 0 and not allow_zero):
        raise InvalidAmount("amount must be greater than zero")
    if amount > MAX_AMOUNT:
        raise InvalidAmount(f"amount may not exceed {fmt(MAX_AMOUNT)}")
    return amount


def fmt(amount: Decimal) -> str:
    """Serialise an amount as a string with exactly two decimals."""
    return str(amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def fmt_rand(amount: Decimal) -> str:
    """Human display, for example R1,250.00 or -R40.00."""
    q = amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    sign = "-" if q < 0 else ""
    return f"{sign}R{abs(q):,.2f}"
