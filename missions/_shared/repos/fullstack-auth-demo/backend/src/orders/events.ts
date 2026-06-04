import { z } from "zod";

/**
 * Order-lifecycle events emitted by the payments pipeline.
 *
 * A `refund` event carries the monetary amount to credit back to the
 * customer. The amount must be a number so the downstream ledger can do
 * arithmetic on it (formatting, summing, comparing against the original
 * charge). Anything else is a malformed event the boundary should reject.
 */
export const refundEventSchema = z.object({
  kind: z.literal("refund"),
  orderId: z.string(),
  // NOTE: `amount` is loosely typed here. `z.any()` accepts whatever the
  // upstream producer sends, which keeps the parser permissive but pushes
  // every type assumption downstream onto callers like `formatRefund`.
  amount: z.any(),
});

export type RefundEvent = z.infer<typeof refundEventSchema>;

/**
 * Validate an untrusted payload as a refund event.
 *
 * Throws a `ZodError` when the payload does not match the schema.
 */
export function parseRefund(raw: unknown): RefundEvent {
  return refundEventSchema.parse(raw);
}

/**
 * Render a refund as a currency string, e.g. `$12.50`.
 *
 * Assumes `amount` is a finite number — the boundary (`parseRefund`) is
 * responsible for guaranteeing that before we get here.
 */
export function formatRefund(refund: RefundEvent): string {
  return "$" + refund.amount.toFixed(2);
}
