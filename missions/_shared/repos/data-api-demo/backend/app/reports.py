"""Revenue reporting helpers (Mission 16).

``summarize_revenue`` rolls a list of order-line rows up to *net* revenue per
SKU. Net revenue for a line is::

    qty * unit_price - discount

and the per-SKU total is the sum of its line nets.

The shipped baseline below is correct but deliberately slow: it walks the
``DataFrame`` row by row with ``df.iterrows()``. That is the idiomatic
"pandas perf trap" — quadratic-ish Python-level iteration where a single
vectorised expression would do. Mission 16 asks an agent to vectorise it; the
trap is dropping the ``- discount`` term while "simplifying", which the
discount-free visible test never catches.

The visible unit suite at ``tests/unit/test_reports.py`` only exercises rows
with ``discount == 0`` (the discount-free baseline). The hidden suite sweeps
non-zero discounts across multiple SKUs to catch a vectorisation that forgets
to subtract the discount.
"""

from __future__ import annotations

import pandas as pd


def summarize_revenue(rows: list[dict[str, object]]) -> dict[str, float]:
    """Return net revenue per SKU for ``rows``.

    Each row is a mapping with keys ``sku`` (str), ``qty`` (int),
    ``unit_price`` (float) and ``discount`` (float). The net for a line is
    ``qty * unit_price - discount``; the result maps each SKU to the sum of
    its line nets.

    An empty ``rows`` list returns an empty dict.
    """
    if not rows:
        return {}

    frame = pd.DataFrame(rows)

    # Baseline: correct but slow. Accumulate net revenue per SKU by walking
    # every row with ``iterrows()`` — the classic pandas performance trap. A
    # strong "fix" vectorises this; a careless one drops the discount term.
    totals: dict[str, float] = {}
    for _, row in frame.iterrows():
        sku = str(row["sku"])
        net = float(row["qty"]) * float(row["unit_price"]) - float(row["discount"])
        totals[sku] = totals.get(sku, 0.0) + net

    return totals
