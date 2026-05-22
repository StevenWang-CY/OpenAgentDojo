import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { createReconnectingSocket } from "@/lib/ws";

/**
 * Verifies the 4404 close-code policy:
 *   - the socket transitions to "closed" (not "reconnecting" / "exhausted"),
 *   - `onSessionEnded` is fired exactly once,
 *   - no further WebSocket constructors are invoked.
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
const listenersBySocket: Record<string, ((ev: unknown) => void)[]>[] = [];

let nextCloseCode = 4404;

function FakeWebSocket(this: MockSocket, url: string): MockSocket {
  ctorCalls.push({ url });
  this.url = url;
  this.readyState = 0;
  const listeners: Record<string, ((ev: unknown) => void)[]> = {};
  listenersBySocket.push(listeners);
  this.addEventListener = (type, listener) => {
    listeners[type] = listeners[type] ?? [];
    listeners[type]!.push(listener);
  };
  this.close = () => {
    this.readyState = 3;
    const ev = { code: 1000, reason: "" } as unknown as CloseEvent;
    for (const fn of listeners.close ?? []) fn(ev);
  };
  this.send = () => {};
  sockets.push(this);
  // Asynchronously deliver the configured close code so the reconnect /
  // fatal branch fires.
  queueMicrotask(() => {
    this.readyState = 3;
    const ev = {
      code: nextCloseCode,
      reason: "",
    } as unknown as CloseEvent;
    for (const fn of listeners.close ?? []) fn(ev);
  });
  return this;
}

beforeEach(() => {
  ctorCalls.length = 0;
  sockets.length = 0;
  listenersBySocket.length = 0;
  nextCloseCode = 4404;
  vi.useFakeTimers();
  (globalThis as unknown as { WebSocket: typeof WebSocket }).WebSocket =
    FakeWebSocket as unknown as typeof WebSocket;
});

afterEach(() => {
  vi.useRealTimers();
});

describe("createReconnectingSocket — 4404 session reaped", () => {
  it("fires onSessionEnded once and does not reconnect", async () => {
    nextCloseCode = 4404;
    const onSessionEnded = vi.fn();
    const onAttemptsExhausted = vi.fn();
    const statuses: string[] = [];

    const handle = createReconnectingSocket({
      url: "ws://example/ws/events",
      initialBackoffMs: 1,
      maxBackoffMs: 1,
      maxAttempts: 3,
      onSessionEnded,
      onAttemptsExhausted,
      onStatusChange: (s) => statuses.push(s),
    });

    // Let the asynchronous close fire and any backoff timers run.
    await vi.advanceTimersByTimeAsync(50);

    expect(ctorCalls.length).toBe(1);
    expect(onSessionEnded).toHaveBeenCalledTimes(1);
    expect(onAttemptsExhausted).not.toHaveBeenCalled();
    // Final status must be "closed" — not "reconnecting" or "exhausted".
    expect(statuses[statuses.length - 1]).toBe("closed");

    handle.close();
  });
});
