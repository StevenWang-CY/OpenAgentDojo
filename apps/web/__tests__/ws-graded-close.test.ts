import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";

const getWsToken = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getWsToken: (...args: unknown[]) => getWsToken(...args),
  };
});

import { ApiError } from "@/lib/api";
import { createReconnectingSocket } from "@/lib/ws";

/**
 * P1 — the events WS closes 1000/"graded" on `submission.graded`. That is a
 * NORMAL terminal close: the stream is done. The socket must tear down
 * cleanly — no reconnect, no `onAttemptsExhausted`, no spurious "Lost
 * connection… Refresh" toast.
 *
 * Pre-fix `ws.ts` did not treat 1000 as terminal, so `onClose` fell through
 * to `scheduleReconnect()`: the FE re-handshook, backfill re-delivered
 * `graded`, the backend re-closed 1000, and the loop burned `maxAttempts`
 * and fired `onAttemptsExhausted` on every successful completion.
 *
 * Also verifies (per the coordinated backend change) that a 4401 close
 * re-mints via the token endpoint and reconnects, while a 1008 stays fatal.
 */

type MockSocket = {
  url: string;
  readyState: number;
  close: (code?: number, reason?: string) => void;
  addEventListener: (type: string, listener: (ev: unknown) => void) => void;
  send: (data: string) => void;
};

const ctorCalls: { url: string }[] = [];
const sockets: MockSocket[] = [];

// Per-test override for the close (code, reason) the FakeWebSocket emits via
// the queued microtask.
let nextCloseCode = 1000;
let nextCloseReason = "graded";

function FakeWebSocket(this: MockSocket, url: string): MockSocket {
  ctorCalls.push({ url });
  this.url = url;
  this.readyState = 0;
  const listeners: Record<string, ((ev: unknown) => void)[]> = {};
  this.addEventListener = (type, listener) => {
    listeners[type] = listeners[type] ?? [];
    listeners[type]!.push(listener);
  };
  this.close = () => {
    this.readyState = 3;
    const ev = { code: 1000, reason: "client_closed" } as unknown as CloseEvent;
    for (const fn of listeners.close ?? []) fn(ev);
  };
  this.send = () => {};
  sockets.push(this);
  const code = nextCloseCode;
  const reason = nextCloseReason;
  queueMicrotask(() => {
    this.readyState = 3;
    const ev = { code, reason } as unknown as CloseEvent;
    for (const fn of listeners.close ?? []) fn(ev);
  });
  return this;
}

beforeEach(() => {
  ctorCalls.length = 0;
  sockets.length = 0;
  nextCloseCode = 1000;
  nextCloseReason = "graded";
  getWsToken.mockReset();
  vi.useFakeTimers();
  (globalThis as unknown as { WebSocket: typeof WebSocket }).WebSocket =
    FakeWebSocket as unknown as typeof WebSocket;
});

afterEach(() => {
  vi.useRealTimers();
});

describe("createReconnectingSocket — 1000/'graded' terminal close", () => {
  it("tears down without reconnecting or firing onAttemptsExhausted", async () => {
    nextCloseCode = 1000;
    nextCloseReason = "graded";
    const onAttemptsExhausted = vi.fn();
    const onGraded = vi.fn();
    const statuses: string[] = [];

    const handle = createReconnectingSocket({
      url: "ws://example/ws/events",
      initialBackoffMs: 1,
      maxBackoffMs: 1,
      maxAttempts: 3,
      onAttemptsExhausted,
      onGraded,
      onStatusChange: (s) => statuses.push(s),
    });

    // Let the async close + any (incorrect) backoff timers run.
    await vi.advanceTimersByTimeAsync(50);

    // Exactly one socket was constructed — no reconnect handshake.
    expect(ctorCalls.length).toBe(1);
    expect(onAttemptsExhausted).not.toHaveBeenCalled();
    expect(onGraded).toHaveBeenCalledTimes(1);
    // Final status is "closed" — not "reconnecting" / "exhausted".
    expect(statuses[statuses.length - 1]).toBe("closed");
    expect(statuses).not.toContain("exhausted");

    handle.close();
  });

  it("does NOT treat a generic 1000 (no 'graded' reason) as terminal", async () => {
    // A bare 1000 without the "graded" reason is an abnormal-ish normal close
    // and must still reconnect via the standard backoff path so the genuine
    // reconnect behaviour is preserved.
    nextCloseCode = 1000;
    nextCloseReason = "";
    const onGraded = vi.fn();

    const handle = createReconnectingSocket({
      url: "ws://example/ws/events",
      initialBackoffMs: 1,
      maxBackoffMs: 1,
      maxAttempts: 2,
      onGraded,
    });

    await vi.advanceTimersByTimeAsync(50);

    // It reconnected at least once (more than the initial constructor call).
    expect(ctorCalls.length).toBeGreaterThanOrEqual(2);
    expect(onGraded).not.toHaveBeenCalled();

    handle.close();
  });
});

describe("createReconnectingSocket — 4401 re-mint vs 1008 fatal", () => {
  it("re-mints a fresh token and reconnects on a 4401 close", async () => {
    // The first socket closes 4401 (token expired). The re-mint resolves a
    // fresh token; the *second* socket then closes 1000/"graded" so the loop
    // terminates deterministically instead of 4401-ing forever (which would
    // exhaust the mock and leave a dangling reconnect timer).
    nextCloseCode = 4401;
    nextCloseReason = "";
    getWsToken.mockImplementation(async () => {
      // From the second connect onward, close normally so the test ends.
      nextCloseCode = 1000;
      nextCloseReason = "graded";
      return { token: "fresh-token" };
    });

    const handle = createReconnectingSocket({
      url: "ws://example/ws/events",
      token: "stale-token",
      sessionId: "session-1",
      initialBackoffMs: 1,
      maxBackoffMs: 1,
      maxAttempts: 3,
    });

    // First connect → 4401 close → getWsToken() → reconnect with new token →
    // second connect closes 1000/"graded" → terminal.
    await vi.advanceTimersByTimeAsync(50);

    expect(getWsToken).toHaveBeenCalledWith("session-1");
    expect(ctorCalls.length).toBeGreaterThanOrEqual(2);
    // The reconnect handshake carried the freshly-minted token.
    expect(ctorCalls[1]!.url).toContain("token=fresh-token");

    handle.close();
  });

  it("treats 1008 as fatal — no re-mint, no reconnect", async () => {
    nextCloseCode = 1008;
    nextCloseReason = "";
    const onAttemptsExhausted = vi.fn();
    const statuses: string[] = [];

    const handle = createReconnectingSocket({
      url: "ws://example/ws/events",
      token: "stale-token",
      sessionId: "session-1",
      initialBackoffMs: 1,
      maxBackoffMs: 1,
      maxAttempts: 3,
      onAttemptsExhausted,
      onStatusChange: (s) => statuses.push(s),
    });

    await vi.advanceTimersByTimeAsync(50);

    expect(ctorCalls.length).toBe(1);
    expect(getWsToken).not.toHaveBeenCalled();
    expect(onAttemptsExhausted).not.toHaveBeenCalled();
    expect(statuses[statuses.length - 1]).toBe("closed");

    handle.close();
  });

  it("bails to onAuthFailure (no loop) when the 4401 re-mint hits a 401", async () => {
    nextCloseCode = 4401;
    nextCloseReason = "";
    getWsToken.mockRejectedValue(new ApiError("unauthorized", 401, null));

    const onAuthFailure = vi.fn();
    const onAttemptsExhausted = vi.fn();
    const statuses: string[] = [];

    const handle = createReconnectingSocket({
      url: "ws://example/ws/events",
      token: "stale-token",
      sessionId: "session-1",
      initialBackoffMs: 1,
      maxBackoffMs: 1,
      maxAttempts: 3,
      onAuthFailure,
      onAttemptsExhausted,
      onStatusChange: (s) => statuses.push(s),
    });

    await vi.advanceTimersByTimeAsync(50);

    expect(onAuthFailure).toHaveBeenCalledTimes(1);
    expect(onAttemptsExhausted).not.toHaveBeenCalled();
    expect(statuses).toContain("exhausted");

    handle.close();
  });
});
