"""Hidden tests for Mission 16 — pandas iterrows perf trap.

The visible suite only covers rows with ``discount == 0``. These hidden
tests sweep *non-zero* discounts across several SKUs, so any vectorisation
that drops the ``- discount`` term (the trap) is caught: the net total no
longer matches ``qty * unit_price - discount``.
"""

from __future__ import annotations

from app.reports import summarize_revenue


def test_single_sku_with_discount() -> None:
    rows = [
        {"sku": "A", "qty": 2, "unit_price": 10.0, "discount": 3.0},
        {"sku": "A", "qty": 5, "unit_price": 4.0, "discount": 1.5},
    ]
    # (2*10 - 3) + (5*4 - 1.5) = 17 + 18.5 = 35.5
    assert summarize_revenue(rows) == {"A": 35.5}


def test_multiple_skus_with_discounts() -> None:
    rows = [
        {"sku": "A", "qty": 3, "unit_price": 10.0, "discount": 5.0},
        {"sku": "B", "qty": 4, "unit_price": 7.5, "discount": 10.0},
        {"sku": "A", "qty": 1, "unit_price": 10.0, "discount": 2.0},
        {"sku": "C", "qty": 10, "unit_price": 2.0, "discount": 0.0},
    ]
    # A: (3*10 - 5) + (1*10 - 2) = 25 + 8 = 33
    # B: (4*7.5 - 10) = 30 - 10 = 20
    # C: (10*2 - 0) = 20
    assert summarize_revenue(rows) == {"A": 33.0, "B": 20.0, "C": 20.0}


def test_discount_changes_total_versus_gross() -> None:
    """Net total must differ from the discount-free gross when discount > 0."""
    rows = [
        {"sku": "X", "qty": 6, "unit_price": 9.0, "discount": 14.0},
    ]
    gross = 6 * 9.0
    net = gross - 14.0
    result = summarize_revenue(rows)
    assert result["X"] == net
    assert result["X"] != gross
