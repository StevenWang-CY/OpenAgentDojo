# 16 — python-pandas-perf-trap

Author notes (not user-facing).

## Premise

`data-api-demo` gains `app/reports.py::summarize_revenue(rows)`, which
rolls order-line rows up to *net* revenue per SKU
(`qty * unit_price - discount`). The shipped baseline is correct but slow:
it walks the DataFrame with `df.iterrows()` — the classic pandas
performance trap.

## Failure mode

The agent (`agent_patch.diff`) vectorises the loop into a group-by on
`qty * unit_price` but drops the `- discount` term. The visible suite at
`tests/unit/test_reports.py` only feeds discount-free rows, so the totals
still match and the diff reads like a clean performance win. Orders with a
non-zero discount now over-report their net revenue.

## Ideal

Keep the vectorisation but restore the discount:
`(frame["qty"] * frame["unit_price"] - frame["discount"]).groupby(frame["sku"]).sum()`,
plus a regression test with non-zero discounts across multiple SKUs.

## Files touched in the repo pack

- `app/reports.py` — the feature with the slow-but-correct baseline
  (committed to the pack baseline; visible-green).
- `tests/unit/test_reports.py` — visible discount-free tests.

## Hidden tests

`hidden_tests/test_reports_hidden.py` (run via the shared pytest runner) —
three probes that feed non-zero discounts across single and multiple SKUs
and assert the exact net totals, plus one that asserts the net differs from
the discount-free gross. All fail when `- discount` is dropped.

## Verified end-to-end

- baseline: visible PASS (iterrows correct)
- agent_patch: visible PASS, hidden FAIL (discount dropped)
- ideal_solution: visible PASS, hidden PASS, mypy + ruff clean
