"""Visible unit tests for ``app.calc.calculate_price``.

These exercise the *paths that currently work* on the initial commit:
zero, one, two units (no volume discount) and the rejection of negative
quantities and float unit prices. The qty-3 / qty-100 cases that catch
the off-by-one volume-discount bug live in the hidden suite — that's
how the supervisor learns to write a regression test for the gap.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.calc import calculate_price


def test_zero_qty_is_zero() -> None:
    assert calculate_price(0, Decimal("10.00")) == Decimal("0.00")


def test_one_unit_at_ten_is_ten() -> None:
    assert calculate_price(1, Decimal("10.00")) == Decimal("10.00")


def test_two_units_at_ten_is_twenty_no_discount() -> None:
    assert calculate_price(2, Decimal("10.00")) == Decimal("20.00")


def test_accepts_string_unit_price() -> None:
    assert calculate_price(1, "12.50") == Decimal("12.50")


def test_accepts_int_unit_price() -> None:
    assert calculate_price(2, 7) == Decimal("14.00")


def test_rejects_float_unit_price() -> None:
    with pytest.raises(TypeError):
        calculate_price(1, 9.99)  # type: ignore[arg-type]


def test_rejects_negative_qty() -> None:
    with pytest.raises(ValueError):
        calculate_price(-1, Decimal("10.00"))


def test_rejects_negative_unit_price() -> None:
    with pytest.raises(ValueError):
        calculate_price(1, Decimal("-1.00"))
