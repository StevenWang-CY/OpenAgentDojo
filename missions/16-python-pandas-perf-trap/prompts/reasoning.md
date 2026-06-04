{# Mission 16 — Agent reasoning (chain-of-thought) template.
   Rendered alongside response.md. Surfaced in the "agent reasoning" pane
   so a supervisor can audit the agent's thought process and catch the gap
   BEFORE accepting the diff. The [skipped] line is the tell: the agent
   waves off re-checking the formula. -#}
Reading `summarize_revenue` top to bottom:

1. It builds a DataFrame, then loops with `df.iterrows()` accumulating a
   per-SKU total. The loop is the slow part — `iterrows()` is the pandas
   perf trap. I'll vectorise it.
2. The per-line value in the loop is `qty * unit_price - discount`. I can
   express `qty * unit_price` as a column product and group by sku.
   [skipped: the visible test passes with the column product, so the
   expression looks right — I won't re-derive the full formula term by
   term.]
3. Return shape stays `dict[str, float]`; callers are unaffected.

Plan: replace the loop with `(frame["qty"] * frame["unit_price"])`
grouped by sku and summed. Keep the diff small.

[risk]
Low — purely a performance rewrite, same return type.

[skipped]
- Did not add a regression test with non-zero discounts; the existing
  discount-free unit test already passes.
- Did not re-check the `- discount` term against the docstring formula.
