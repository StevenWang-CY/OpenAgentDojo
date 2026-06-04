import { describe, expect, it } from "vitest";

import { formatRefund, parseRefund } from "../../orders/events";

describe("refund events — happy path", () => {
  it("parses a well-formed refund event", () => {
    const refund = parseRefund({
      kind: "refund",
      orderId: "ord-1001",
      amount: 12.5,
    });
    expect(refund.kind).toBe("refund");
    expect(refund.orderId).toBe("ord-1001");
    expect(refund.amount).toBe(12.5);
  });

  it("formats a parsed refund as a currency string", () => {
    const refund = parseRefund({
      kind: "refund",
      orderId: "ord-1002",
      amount: 12.5,
    });
    expect(formatRefund(refund)).toBe("$12.50");
  });

  it("rejects a payload whose kind is not 'refund'", () => {
    expect(() =>
      parseRefund({ kind: "charge", orderId: "ord-1003", amount: 5 }),
    ).toThrow();
  });
});
