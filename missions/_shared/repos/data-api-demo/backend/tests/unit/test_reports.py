"""Visible unit tests for the revenue reporter (Mission 16).

These cover the discount-free baseline only: every row has ``discount == 0``,
so a vectorisation that forgets the ``- discount`` term still passes here. The
non-zero-discount sweep that catches that trap lives in the hidden suite.
"""

from __future__ import annotations

from app.reports import summarize_revenue


def test_single_sku_no_discount() -> None:
    rows = [
        {"sku": "A", "qty": 2, "unit_price": 10.0, "discount": 0.0},
        {"sku": "A", "qty": 3, "unit_price": 10.0, "discount": 0.0},
    ]
    assert summarize_revenue(rows) == {"A": 50.0}


def test_multiple_skus_no_discount() -> None:
    rows = [
        {"sku": "A", "qty": 1, "unit_price": 10.0, "discount": 0.0},
        {"sku": "B", "qty": 4, "unit_price": 5.0, "discount": 0.0},
        {"sku": "A", "qty": 2, "unit_price": 10.0, "discount": 0.0},
    ]
    assert summarize_revenue(rows) == {"A": 30.0, "B": 20.0}


def test_empty_rows() -> None:
    assert summarize_revenue([]) == {}
