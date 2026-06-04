# 17 — python-fastapi-pydantic-coercion

Author notes (not user-facing).

## Premise

`data-api-demo` gains `app/orders_api.py` with an `OrderPayload(BaseModel)`
and two pricing entry points, `place_order` and `quote_order`. The shipped
baseline is **correct**: both functions run the payload through the model
(Pydantic coerces `"42"` to `42`) and both reject `qty <= 0`, so a quote
always equals the matching placement. The catch is that the
validate-then-check block is duplicated verbatim in both functions — a smell
an agent is asked to clean up.

## Failure mode

The agent (`agent_patch.diff`) "DRYs" the duplication by gutting
`quote_order` to a raw `int(payload["qty"])` read, which drops the model's
coercion and the `qty > 0` rule. The visible suite only feeds an integer
`qty`, where the lighter read still agrees, so the diff reads like a clean
simplification. But a non-positive `qty` is now rejected by `place_order`
and priced by `quote_order` — the two diverge.

## Ideal

Extract a single `_line_total` helper that validates through the model and
enforces `qty > 0` once, and have both `place_order` and `quote_order`
delegate to it. One coercion + validation path; the two can never diverge.
Plus a regression test covering string coercion and non-positive rejection.

## Files touched in the repo pack

- `app/orders_api.py` — the feature with the duplicated-but-correct baseline
  (committed to the pack baseline; visible-green and hidden-green).
- `tests/unit/test_orders_api.py` — visible integer-qty tests.

## Hidden tests

`hidden_tests/test_orders_api_hidden.py` (run via the shared pytest runner)
— three probes: a string `qty` quotes the same as it places, the string is
priced as an int (never repeated), and a non-positive `qty` is rejected by
both paths. The correct baseline (and the ideal) pass all three; the agent's
gutted quote path fails the non-positive probe.

## Verified end-to-end

- baseline: visible PASS, hidden PASS (both paths consistent)
- agent_patch: visible PASS, hidden FAIL (non-positive diverges)
- ideal_solution: visible PASS, hidden PASS, mypy + ruff clean
