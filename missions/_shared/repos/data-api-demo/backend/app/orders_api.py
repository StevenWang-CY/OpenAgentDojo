"""Order placement + quoting (Mission 17).

Two entry points compute the line total for an incoming order payload:

  * ``place_order(payload)`` validates the payload through the
    ``OrderPayload`` Pydantic model, then prices the *typed* ``qty``.
  * ``quote_order(payload)`` is the same calculation for a quote you don't
    persist — a quote is just a placement you don't save.

The contract is that a quote and a placement for the *same payload* return
the *same* total. The shipped baseline below honours that: **both** paths run
the payload through ``OrderPayload`` (so Pydantic coerces a string ``"42"``
to the int ``42``) and **both** reject non-positive quantities (``qty <= 0``).

The duplication is the smell Mission 17 asks an agent to clean up: the
validate-then-check block appears verbatim in both functions. The trap is
"DRYing" it by making ``quote_order`` read ``payload["qty"]`` raw — that
drops the coercion and the ``qty > 0`` rule, so a quote silently diverges
from the matching placement.

The visible suite at ``tests/unit/test_orders_api.py`` feeds an integer
``qty`` where any rewrite happens to agree. The hidden suite feeds a string
``"42"`` and a non-positive ``qty`` to catch a divergence.
"""

from __future__ import annotations

from pydantic import BaseModel

#: Price per unit, in whole currency units.
UNIT_PRICE: int = 7


class OrderPayload(BaseModel):
    """An incoming order line.

    ``qty`` is declared ``int``; Pydantic will coerce string digits like
    ``"42"`` to ``42`` on validation. ``sku`` is a free-form identifier.
    """

    sku: str
    qty: int


def place_order(payload: dict[str, object]) -> int:
    """Validate ``payload`` through ``OrderPayload`` and return the total.

    Coerces ``qty`` to ``int`` via the model and enforces ``qty > 0``.

    Raises:
        ValueError: if ``qty`` is not a positive integer (after coercion).
    """
    order = OrderPayload.model_validate(payload)
    if order.qty <= 0:
        raise ValueError(f"qty must be > 0, got {order.qty}")
    return order.qty * UNIT_PRICE


def quote_order(payload: dict[str, object]) -> int:
    """Return the line total for ``payload`` without persisting it.

    Runs the identical validation as ``place_order`` (same coercion, same
    ``qty > 0`` rule) so a quote always matches the matching placement. The
    block below is duplicated verbatim from ``place_order`` — that is the
    smell, not a behaviour difference.
    """
    order = OrderPayload.model_validate(payload)
    if order.qty <= 0:
        raise ValueError(f"qty must be > 0, got {order.qty}")
    return order.qty * UNIT_PRICE
