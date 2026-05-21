import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
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
  // Schedule an asynchronous "close" so the reconnect path triggers.
  queueMicrotask(() => {
    this.readyState = 3;
    const ev = { code: 1006, reason: "" } as unknown as CloseEvent;
    for (const fn of listeners.close ?? []) fn(ev);
  });
  return this;
}

beforeEach(() => {
  ctorCalls.length = 0;
  sockets.length = 0;
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
