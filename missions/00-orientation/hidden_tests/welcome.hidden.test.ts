/**
 * Hidden test for Mission 00 (orientation).
 *
 * The visible suite only asserts the trailing "!" so the agent's plausible
 * but wrong patch passes. This hidden assertion is the one that catches
 * the missing argument interpolation: any "fix" that ignores ``name``
 * cannot satisfy ``toContain("Alice")``.
 */
import { describe, expect, it } from "vitest";

import { welcomeBanner } from "../../utils/welcome";

describe("welcomeBanner — hidden", () => {
  it("interpolates the name argument", () => {
    const result = welcomeBanner("Alice");
    expect(result).toContain("Alice");
    expect(result).toBe("Hello, Alice!");
  });
});
