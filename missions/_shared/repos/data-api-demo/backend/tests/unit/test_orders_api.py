"""Visible unit tests for order placement + quoting (Mission 17).

These feed an *integer* ``qty`` only, where the validated ``place_order``
path and the raw ``quote_order`` path happen to agree. The string-coercion
and non-positive-qty cases that expose the divergence live in the hidden
suite.
"""

from __future__ import annotations

from app.orders_api import UNIT_PRICE, place_order, quote_order


def test_place_and_quote_agree_for_int_qty() -> None:
    payload = {"sku": "WIDGET", "qty": 42}
    assert place_order(payload) == 42 * UNIT_PRICE
    assert quote_order(payload) == 42 * UNIT_PRICE
    assert place_order(payload) == quote_order(payload)


def test_place_order_prices_typed_qty() -> None:
    assert place_order({"sku": "A", "qty": 3}) == 3 * UNIT_PRICE
