"""Hidden tests for Mission 17 — pydantic silent coercion.

The visible suite only feeds an *integer* ``qty``, where the validated
``place_order`` path and the raw ``quote_order`` path agree. These hidden
tests feed a string ``qty`` and a non-positive ``qty`` to prove the two
paths handle coercion and validation *identically* — a quote must equal the
matching placement, and both must reject the same invalid input.
"""

from __future__ import annotations

import pytest

from app.orders_api import place_order, quote_order


def test_string_qty_quotes_same_as_placement() -> None:
    """A string qty must coerce identically in both paths."""
    payload = {"sku": "WIDGET", "qty": "42"}
    assert quote_order(payload) == place_order(payload)


def test_string_qty_is_priced_as_int_not_repeated() -> None:
    """`"3"` must price as 3 units, never as string repetition."""
    payload = {"sku": "A", "qty": "3"}
    placed = place_order(payload)
    assert quote_order(payload) == placed
    assert isinstance(quote_order(payload), int)


def test_non_positive_qty_rejected_consistently() -> None:
    """Both paths must reject qty <= 0; neither may silently price it."""
    for bad in (0, -5, "0", "-2"):
        payload = {"sku": "A", "qty": bad}
        with pytest.raises(ValueError):
            place_order(payload)
        with pytest.raises(ValueError):
            quote_order(payload)
