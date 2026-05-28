/**
 * P1-5 FE remediation — token-level inline char-diff helpers.
 *
 * The two pure helpers below back the load-bearing-line marker's
 * token-diff overlay. The algorithm is documented in
 * LoadBearingLineMarker.tsx — these tests pin down the algebraic
 * contract so refactors don't silently change the boundary conditions.
 */
import { describe, expect, it } from "vitest";

import {
  computeLoadBearingTokenDiff,
  tokenDiff,
  tokeniseLine,
  type TokenDiffOp,
} from "@/components/report/LoadBearingLineMarker";

describe("tokeniseLine", () => {
  it("keeps every whitespace delimiter as its own token", () => {
    expect(tokeniseLine("foo bar baz")).toEqual(["foo", " ", "bar", " ", "baz"]);
  });

  it("keeps punctuation as its own token", () => {
    expect(tokeniseLine("a,b c")).toEqual(["a", ",", "b", " ", "c"]);
  });

  it("returns an empty array for an empty input", () => {
    expect(tokeniseLine("")).toEqual([]);
  });

  it("handles mixed punctuation and whitespace", () => {
    expect(tokeniseLine("hello, world!")).toEqual([
      "hello",
      ",",
      " ",
      "world",
      "!",
    ]);
  });

  it("preserves leading and trailing whitespace as tokens", () => {
    expect(tokeniseLine("  foo  ")).toEqual([" ", " ", "foo", " ", " "]);
  });

  it("is lossless — joining the tokens reproduces the input", () => {
    const samples = [
      "function readSession(cookie) {",
      "  return cookie === undefined;",
      "if (!session || !session.isValid(Date.now())) {",
      "x,y,z;",
    ];
    for (const s of samples) {
      expect(tokeniseLine(s).join("")).toBe(s);
    }
  });
});

describe("tokenDiff", () => {
  it("flags a single-token swap as removed + added with the surrounding equals", () => {
    const ops = tokenDiff(["a", "x", "b"], ["a", "y", "b"]);
    // The LCS table has multiple optimal paths through ties; both orderings
    // ([equal, removed, added, equal] and [equal, added, removed, equal])
    // are semantically identical. We pin the exact shape the algorithm
    // emits today so refactors are caught, while documenting the
    // semantic guarantee separately.
    expect(ops.length).toBe(4);
    expect(ops[0]).toEqual({ kind: "equal", value: "a" });
    expect(ops[3]).toEqual({ kind: "equal", value: "b" });
    const middle = ops.slice(1, 3);
    const kinds = middle.map((o) => o.kind).sort();
    expect(kinds).toEqual(["added", "removed"]);
    const removed = middle.find((o) => o.kind === "removed");
    const added = middle.find((o) => o.kind === "added");
    expect(removed?.value).toBe("x");
    expect(added?.value).toBe("y");
  });

  it("returns an empty list for two empty inputs", () => {
    expect(tokenDiff([], [])).toEqual([]);
  });

  it("returns all-added when user side is empty", () => {
    expect(tokenDiff([], ["a", " ", "b"])).toEqual<TokenDiffOp[]>([
      { kind: "added", value: "a" },
      { kind: "added", value: " " },
      { kind: "added", value: "b" },
    ]);
  });

  it("returns all-removed when ideal side is empty", () => {
    expect(tokenDiff(["a", " ", "b"], [])).toEqual<TokenDiffOp[]>([
      { kind: "removed", value: "a" },
      { kind: "removed", value: " " },
      { kind: "removed", value: "b" },
    ]);
  });

  it("returns all-equal when inputs are identical", () => {
    expect(tokenDiff(["a", " ", "b"], ["a", " ", "b"])).toEqual<TokenDiffOp[]>([
      { kind: "equal", value: "a" },
      { kind: "equal", value: " " },
      { kind: "equal", value: "b" },
    ]);
  });

  it("handles an inserted token in the middle of a longer line", () => {
    const user = tokeniseLine("if (cookie) return null");
    const ideal = tokeniseLine("if (cookie && cookie.fresh) return null");
    const ops = tokenDiff(user, ideal);
    // The reconstruction of the equal+added tokens (left-to-right)
    // must equal the ideal side, and the equal+removed must equal user.
    const userReconstructed = ops
      .filter((o) => o.kind === "equal" || o.kind === "removed")
      .map((o) => o.value)
      .join("");
    const idealReconstructed = ops
      .filter((o) => o.kind === "equal" || o.kind === "added")
      .map((o) => o.value)
      .join("");
    expect(userReconstructed).toBe("if (cookie) return null");
    expect(idealReconstructed).toBe("if (cookie && cookie.fresh) return null");
  });
});

describe("computeLoadBearingTokenDiff", () => {
  it("returns null when either side is undefined", () => {
    expect(computeLoadBearingTokenDiff(undefined, "x")).toBeNull();
    expect(computeLoadBearingTokenDiff("x", undefined)).toBeNull();
    expect(computeLoadBearingTokenDiff(undefined, undefined)).toBeNull();
  });

  it("returns null when the lines are identical", () => {
    expect(computeLoadBearingTokenDiff("a b c", "a b c")).toBeNull();
  });

  it("returns null when either side is over the 400-character bail-out", () => {
    const longLine = "x".repeat(401);
    expect(computeLoadBearingTokenDiff(longLine, "short")).toBeNull();
    expect(computeLoadBearingTokenDiff("short", longLine)).toBeNull();
  });

  it("collapses to a single removed+added pair when token count exceeds 100", () => {
    // 200 distinct two-char tokens separated by spaces — well under the
    // 400-char hard cap, but well over the 100-token soft cap. The
    // helper should bail to the coarse pair rather than running the
    // O(N²) LCS.
    const tokens: string[] = [];
    for (let i = 0; i < 120; i += 1) tokens.push(`t${i}`);
    const user = tokens.join(" ").slice(0, 399);
    const ideal = user + "Z"; // append something so they differ
    // Both must fit under 400 chars for the helper to even attempt a
    // token diff (rather than the "too long, give up" null path).
    if (user.length <= 400 && ideal.length <= 400) {
      const ops = computeLoadBearingTokenDiff(user, ideal);
      expect(ops).not.toBeNull();
      if (ops) {
        // When the soft cap trips, the helper falls back to the coarse
        // pair: one removed (the whole user line) + one added (whole
        // ideal). That's two ops total.
        expect(ops).toHaveLength(2);
        expect(ops[0]?.kind).toBe("removed");
        expect(ops[1]?.kind).toBe("added");
      }
    }
  });
});
