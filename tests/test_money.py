from decimal import Decimal

import pytest

from mockbank.money import InvalidAmount, fmt, fmt_rand, parse_amount


@pytest.mark.parametrize(
    "raw,expected", [("500.00", "500.00"), ("500", "500.00"), ("0.1", "0.10"), (" 12.5 ", "12.50")]
)
def test_parse_valid(raw, expected):
    assert fmt(parse_amount(raw)) == expected


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "1.005", "NaN", "Infinity", "1000000.01", "", None])
def test_parse_invalid(raw):
    with pytest.raises(InvalidAmount):
        parse_amount(raw)


def test_floats_are_rejected():
    with pytest.raises(InvalidAmount):
        parse_amount(0.1)


def test_zero_allowed_when_requested():
    assert parse_amount("0", allow_zero=True) == Decimal("0.00")


def test_formatting():
    assert fmt(Decimal("1234.5")) == "1234.50"
    assert fmt_rand(Decimal("1234.5")) == "R1,234.50"
    assert fmt_rand(Decimal("-40")) == "-R40.00"
