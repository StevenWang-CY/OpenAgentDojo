/**
 * P1 — Scratchpad localStorage scoping regression test.
 *
 * The persist key was previously ``arena:workspace:${sessionId}`` — fine on
 * a single-user machine but leaked workspace state across accounts when
 * two users signed in to the same browser. This test asserts the key now
 * includes the user id (``arena:workspace:${userId}:${sessionId}``) and
 * falls back to ``"anon"`` when no user id is threaded through.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { useWorkspaceStore } from "@/stores/workspaceStore";

const SESSION = "11111111-2222-3333-4444-555555555555";
const USER_A = "user-a-uuid";
const USER_B = "user-b-uuid";

describe("workspaceStore — localStorage key scoping (P1)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("writes under arena:workspace:${userId}:${sessionId} when a user id is supplied", () => {
    const store = useWorkspaceStore(SESSION, USER_A);
    // Trigger a state mutation so the persist middleware flushes the
    // partialised slice to localStorage.
    store.getState().setScratchpadOpen(true);

    const expectedKey = `arena:workspace:${USER_A}:${SESSION}`;
    expect(localStorage.getItem(expectedKey)).not.toBeNull();
    // Defensive — make sure the previous unscoped key isn't written too.
    expect(localStorage.getItem(`arena:workspace:${SESSION}`)).toBeNull();
  });

  it('falls back to "anon" when no user id is supplied', () => {
    const store = useWorkspaceStore(SESSION);
    store.getState().setScratchpadOpen(true);

    expect(
      localStorage.getItem(`arena:workspace:anon:${SESSION}`),
    ).not.toBeNull();
  });

  it("two distinct user ids on the same session get separate keys", () => {
    const storeA = useWorkspaceStore(SESSION, USER_A);
    const storeB = useWorkspaceStore(SESSION, USER_B);
    expect(storeA).not.toBe(storeB);

    storeA.getState().setScratchpadOpen(true);
    storeB.getState().setScratchpadOpen(false);

    expect(
      localStorage.getItem(`arena:workspace:${USER_A}:${SESSION}`),
    ).not.toBeNull();
    expect(
      localStorage.getItem(`arena:workspace:${USER_B}:${SESSION}`),
    ).not.toBeNull();
  });
});
