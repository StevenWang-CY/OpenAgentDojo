import { describe, expect, it } from "vitest";

import {
  parseSessionCookie,
  Session,
  signSession,
  sessionFromPayload,
} from "../../auth/session";

describe("signSession / parseSessionCookie", () => {
  it("round-trips a payload through sign and parse", () => {
    const exp = Date.now() + 60_000;
    const cookie = signSession({ userId: "u1", exp });
    const parsed = parseSessionCookie(cookie);
    expect(parsed).not.toBeNull();
    expect(parsed?.userId).toBe("u1");
    expect(parsed?.exp).toBe(exp);
  });

  it("returns null for empty or malformed cookies", () => {
    expect(parseSessionCookie("")).toBeNull();
    expect(parseSessionCookie("not-a-cookie")).toBeNull();
    expect(parseSessionCookie(".only-sig")).toBeNull();
    expect(parseSessionCookie("only-payload.")).toBeNull();
  });

  it("rejects a cookie whose signature does not match", () => {
    const cookie = signSession({ userId: "u1", exp: Date.now() + 60_000 });
    const tampered = cookie.slice(0, -1) + (cookie.endsWith("A") ? "B" : "A");
    expect(parseSessionCookie(tampered)).toBeNull();
  });
});

describe("Session.isValid", () => {
  it("returns true when exp is in the future", () => {
    const s = sessionFromPayload({ userId: "u1", exp: 2_000 });
    expect(s.isValid(1_000)).toBe(true);
  });

  it("returns false when exp is in the past", () => {
    const s = sessionFromPayload({ userId: "u1", exp: 1_000 });
    expect(s.isValid(2_000)).toBe(false);
  });

  it("returns false when exp equals now (strict greater-than)", () => {
    const s = sessionFromPayload({ userId: "u1", exp: 1_000 });
    expect(s.isValid(1_000)).toBe(false);
  });

  it("constructs from a payload object", () => {
    const s = new Session({ userId: "alice", exp: 12345 });
    expect(s.userId).toBe("alice");
    expect(s.exp).toBe(12345);
  });
});
