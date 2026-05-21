# Pricing rules

Single source of truth for the order-line price calculator
(`app.calc.calculate_price`). The hidden grader for Mission 04 enforces
these rules against a wide grid of quantities and prices.

## Inputs

| Field | Type | Notes |
|-------|------|-------|
| `qty` | `int >= 0` | Negative values raise `ValueError`. |
| `unit` | `Decimal | int | str` | Floats are rejected to avoid silent rounding loss. |

## Output

A `Decimal` quantized to two cents using `ROUND_HALF_UP`.

## Discount table

| Quantity (`qty`) | Discount |
|------------------|----------|
| `0`              | n/a — total is `0.00` |
| `1` – `2`        | none |
| `>= 3`           | **10% volume discount** |

The 10% discount applies to the *whole* subtotal, not to the marginal
units above 3. That's the contract.

## Worked examples

| `qty` | `unit`         | total      | notes |
|-------|----------------|------------|-------|
| 0     | any            | `0.00`     | short-circuit |
| 1     | `10.00`        | `10.00`    | no discount |
| 2     | `10.00`        | `20.00`    | no discount |
| 3     | `10.00`        | `27.00`    | 10% off `30.00` |
| 4     | `10.00`        | `36.00`    | 10% off `40.00` |
| 100   | `1.00`         | `90.00`    | 10% off `100.00` |
| 5     | `9.99`         | `44.96`    | 10% off `49.95`, rounded half-up |

## Anti-patterns

- Hard-coded branches on specific `qty` values (`if qty == 3: …`). The
  hidden grader sweeps `qty ∈ {3, 4, 5, 8, 100}` and `unit ∈ {7, 12.50,
  9.99}`, so an overfit fix that targets the visible failure will fail
  the hidden suite immediately.
- Comparing `unit` as a `float`. Use `Decimal` end-to-end.
