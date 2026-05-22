import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";

const getWsToken = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getWsToken: (...args: unknown[]) => getWsToken(...args),
  };
});

import { buildWsUrl, createReconnectingSocket } from "@/lib/ws";

/**
 * Verify that `createReconnectingSocket` calls `resolveQueryParams` on every
 * (re)connect and threads `last_id` into the WebSocket URL.
 */

type MockSocket = {
  url: string;
  readyState: number;
  close: (code?: number, reason?: string) => void;
  onopen?: ((ev: Event) => void) | null;
  onclose?: ((ev: CloseEvent) => void) | null;
  onerror?: ((ev: Event) => void) | null;
  onmessage?: ((ev: MessageEvent) => void) | null;
  addEventListener: (type: string, listener: (ev: unknown) => void) => void;
  send: (data: string) => void;
};

const ctorCalls: { url: string }[] = [];
const sockets: MockSocket[] = [];
// Per-test override for the close code that FakeWebSocket emits via the
// queued microtask. Defaults to 1006 (generic abnormal close) which
// matches what the original tests expect.
let autoCloseCode = 1006;

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
    const ev = { code: 1006, reason: "" } as unknown as CloseEvent;
    for (const fn of listeners.close ?? []) fn(ev);
  };
  this.send = () => {};
  sockets.push(this);
  const code = autoCloseCode;
  // Schedule an asynchronous "close" so the reconnect path triggers.
  queueMicrotask(() => {
    this.readyState = 3;
    const ev = { code, reason: "" } as unknown as CloseEvent;
    for (const fn of listeners.close ?? []) fn(ev);
  });
  return this;
}

beforeEach(() => {
  ctorCalls.length = 0;
  sockets.length = 0;
  autoCloseCode = 1006;
  getWsToken.mockReset();
  vi.useFakeTimers();
  (globalThis as unknown as { WebSocket: typeof WebSocket }).WebSocket =
    FakeWebSocket as unknown as typeof WebSocket;
});

afterEach(() => {
  vi.useRealTimers();
});

describe("buildWsUrl extras", () => {
  it("appends extras as query params", () => {
    const url = buildWsUrl("/ws/test", "tok", { last_id: 42 });
    expect(url).toContain("token=tok");
    expect(url).toContain("last_id=42");
  });

  it("skips undefined extras", () => {
    const url = buildWsUrl("/ws/test", "tok", { last_id: undefined });
    expect(url).not.toContain("last_id");
  });
});

describe("createReconnectingSocket — last_id on reconnect", () => {
  it("re-resolves query params on every connect attempt", async () => {
    // The resolver reads a closure variable that the test mutates between
    // connect attempts to simulate "events arrived since last connect".
    let lastId = 0;
    let connectCount = 0;
    const handle = createReconnectingSocket({
      url: "ws://example/ws/events",
      resolveQueryParams: () => {
        connectCount += 1;
        // After the first connect resolves params, bump `lastId` so the
        // *next* connect picks up a fresh value rather than a stale ref.
        if (connectCount === 1) {
          // Schedule the mutation after this synchronous resolver call.
          queueMicrotask(() => {
            lastId = 17;
          });
        }
        return lastId > 0 ? { last_id: lastId } : {};
      },
      initialBackoffMs: 1,
      maxBackoffMs: 1,
      maxAttempts: 3,
    });

    // Let the first connect + close + reconnect (with new last_id) fire.
    await vi.advanceTimersByTimeAsync(20);

    expect(ctorCalls.length).toBeGreaterThanOrEqual(2);
    const firstUrl = ctorCalls[0]!.url;
    const secondUrl = ctorCalls[1]!.url;
    expect(firstUrl).not.toContain("last_id=");
    expect(secondUrl).toContain("last_id=17");

    handle.close();
  });

  it("strips an existing last_id from the raw URL so re-injection is clean", async () => {
    const handle = createReconnectingSocket({
      url: "ws://example/ws/events?last_id=99",
      resolveQueryParams: () => ({ last_id: 200 }),
      initialBackoffMs: 1,
      maxBackoffMs: 1,
      maxAttempts: 1,
    });
    await vi.advanceTimersByTimeAsync(5);
    expect(ctorCalls[0]!.url).toContain("last_id=200");
    expect(ctorCalls[0]!.url).not.toContain("last_id=99");
    handle.close();
  });
});

describe("createReconnectingSocket — getWsToken non-ApiError fallback", () => {
  it("does not silently loop and respects maxAttempts when getWsToken throws a generic error", async () => {
    // Drive the close-code through the 4401 (token expired) branch on
    // every reconnect.
    autoCloseCode = 4401;
    // Resolve the token-refresh promise with a non-ApiError. Pre-fix this
    // would have queued another reconnect *without* incrementing the
    // attempt counter — letting the loop run forever. After the fix the
    // socket falls back to `scheduleReconnect` which honours `maxAttempts`.
    getWsToken.mockRejectedValue(new TypeError("boom"));

    const onAttemptsExhausted = vi.fn();
    const onAuthFailure = vi.fn();
    const statuses: string[] = [];
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const handle = createReconnectingSocket({
      url: "ws://example/ws/events",
      token: "stale",
      sessionId: "session-1",
      initialBackoffMs: 1,
      maxBackoffMs: 1,
      maxAttempts: 2,
      onAttemptsExhausted,
      onAuthFailure,
      onStatusChange: (s) => statuses.push(s),
    });

    // Let the reconnect loop spin a few times — each iteration:
    // close(4401) → getWsToken() rejects → console.error → scheduleReconnect.
    for (let i = 0; i < 10; i += 1) {
      await vi.advanceTimersByTimeAsync(5);
    }

    expect(onAuthFailure).not.toHaveBeenCalled();
    // The exhausted callback must fire (we hit maxAttempts within a
    // bounded number of cycles instead of looping forever).
    expect(onAttemptsExhausted).toHaveBeenCalledTimes(1);
    expect(statuses).toContain("exhausted");
    expect(errSpy).toHaveBeenCalled();

    errSpy.mockRestore();
    handle.close();
  });
});
