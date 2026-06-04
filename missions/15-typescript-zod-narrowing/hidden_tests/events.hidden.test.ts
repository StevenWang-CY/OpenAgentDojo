/**
 * Hidden tests for Mission 15 — TypeScript / zod narrowing.
 *
 * Copied into `backend/src/tests/hidden/` by the grader at submit time.
 * They exercise the failure mode the agent is designed to miss: the
 * refund schema types `amount` as `z.any()`, so a non-numeric amount
 * sails through validation and only blows up (or silently becomes `$NaN`)
 * downstream in `formatRefund`.
 *
 * The visible suite (`backend/src/tests/unit/events.test.ts`) only feeds
 * well-formed numeric amounts, so it never surfaces the gap. The cases
 * below assert that the boundary REJECTS a malformed amount.
 */
import { ZodError } from "zod";
import { describe, expect, it } from "vitest";

import { formatRefund, parseRefund } from "../../orders/events";

describe("refund events — hidden boundary validation", () => {
  it("rejects a string amount at parse time", () => {
    expect(() =>
      parseRefund({ kind: "refund", orderId: "ord-9001", amount: "oops" }),
    ).toThrow(ZodError);
  });

  it("rejects an object amount at parse time", () => {
    expect(() =>
      parseRefund({ kind: "refund", orderId: "ord-9002", amount: {} }),
    ).toThrow(ZodError);
  });

  it("rejects a NaN amount at parse time", () => {
    expect(() =>
      parseRefund({ kind: "refund", orderId: "ord-9003", amount: Number.NaN }),
    ).toThrow(ZodError);
  });

  it("still accepts and formats a valid numeric amount", () => {
    const refund = parseRefund({
      kind: "refund",
      orderId: "ord-9004",
      amount: 7.05,
    });
    expect(refund.amount).toBe(7.05);
    expect(formatRefund(refund)).toBe("$7.05");
  });
});
