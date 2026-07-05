from decimal import Decimal

from app.domain.money import parse_money


def test_parse_russian_money() -> None:
    assert parse_money("1 234 567,89 руб.") == Decimal("1234567.89")
    assert parse_money("1\u00a0234,00") == Decimal("1234.00")


def test_parse_missing_money() -> None:
    assert parse_money("-") is None
    assert parse_money(None) is None

