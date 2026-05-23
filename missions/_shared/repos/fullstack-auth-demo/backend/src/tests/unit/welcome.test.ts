/**
 * Visible test for Mission 00 (orientation tutorial).
 *
 * The tutorial intentionally ships a permissive visible test (only the
 * trailing exclamation mark is asserted) so a quick "looks right" read
 * of the agent's patch passes — the bug surfaces only in the hidden
 * suite. This is the smallest reproduction of the visible/hidden split
 * the dojo trains supervisors against.
 */
import { describe, expect, it } from "vitest";

import { welcomeBanner } from "../../utils/welcome";

describe("welcomeBanner — visible", () => {
  it("ends with the welcoming punctuation", () => {
    const result = welcomeBanner("Alice");
    expect(result).toMatch(/!$/);
  });
});
