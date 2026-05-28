/**
 * P1 — LSP chip subscribes to state transitions event-driven (not polled).
 *
 * Previously the CodeEditor footer chip ran a setTimeout(pollTick, 250)
 * loop until the client settled to ready / error. The chip lagged by up
 * to ~250 ms on every state flip and the polling kept ticking through
 * the cold-start window even when the language server was idle. This
 * test asserts the manager fans a state transition out to per-acquire
 * subscribers synchronously the moment the underlying client emits one
 * — no advance of fake timers required.
 */
import { describe, expect, it, vi } from "vitest";
import { createLSPManager } from "@/lib/lsp/manager";
import type {
  CreateLSPClientOptions,
  LSPClientHandle,
  LSPLanguage,
  LSPState,
} from "@/lib/lsp/client";

function makeFactory(): {
  factory: (opts: CreateLSPClientOptions) => LSPClientHandle;
  emit: (state: LSPState) => void;
  current: () => LSPState;
} {
  let onStateChange: ((s: LSPState) => void) | undefined;
  let current: LSPState = { kind: "connecting", language: "python" };
  const factory = (opts: CreateLSPClientOptions): LSPClientHandle => {
    onStateChange = opts.onStateChange;
    return {
      language: opts.language,
      state: () => current,
      openDocument: () => undefined,
      changeDocument: () => undefined,
      closeDocument: () => undefined,
      requestCompletion: () => Promise.resolve(null),
      requestHover: () => Promise.resolve(null),
      onDiagnostics: () => () => undefined,
      close: () => undefined,
      retry: () => undefined,
    };
  };
  const emit = (state: LSPState): void => {
    current = state;
    onStateChange?.(state);
  };
  return { factory, emit, current: () => current };
}

describe("LSPManager.acquire — onStateChange subscription (P1)", () => {
  it("seeds the subscriber with the initial state synchronously on acquire", async () => {
    const { factory } = makeFactory();
    const manager = createLSPManager({
      sessionId: "session-1",
      fetchToken: () => Promise.resolve("ws-token"),
      factory,
    });
    const seen: LSPState[] = [];
    const client = await manager.acquire("python", {
      onStateChange: (s) => seen.push(s),
    });
    expect(client).not.toBeNull();
    // At least one transition fired synchronously — the seed (current
    // client.state()) is delivered without any timer advance.
    expect(seen.length).toBeGreaterThanOrEqual(1);
    expect(seen[0]!.kind).toBe("connecting");
  });

  it("fans a client state transition out to a per-acquire subscriber immediately (no 250ms wait)", async () => {
    vi.useFakeTimers();
    try {
      const { factory, emit } = makeFactory();
      const manager = createLSPManager({
        sessionId: "session-1",
        fetchToken: () => Promise.resolve("ws-token"),
        factory,
      });
      const seen: LSPState[] = [];
      await manager.acquire("python", {
        onStateChange: (s) => seen.push(s),
      });
      const beforeEmit = seen.length;

      // The client transitions from connecting → ready. The subscriber
      // must see the new state on the same microtask — NOT after a
      // 250 ms timer advance.
      emit({ kind: "ready", language: "python", coldStartMs: 42 });

      expect(seen.length).toBe(beforeEmit + 1);
      expect(seen[seen.length - 1]!.kind).toBe("ready");

      // Belt-and-braces: even if we never advance fake timers the chip
      // is already on the latest state. Previously the editor needed
      // 250 ms tick before the chip flipped to green.
      vi.advanceTimersByTime(0);
      expect(seen[seen.length - 1]!.kind).toBe("ready");
    } finally {
      vi.useRealTimers();
    }
  });

  it("detaches the subscriber via manager.unsubscribe", async () => {
    const { factory, emit } = makeFactory();
    const manager = createLSPManager({
      sessionId: "session-1",
      fetchToken: () => Promise.resolve("ws-token"),
      factory,
    });
    const seen: LSPState[] = [];
    const cb = (s: LSPState): void => {
      seen.push(s);
    };
    await manager.acquire("python", { onStateChange: cb });
    const beforeUnsub = seen.length;
    manager.unsubscribe("python", cb);
    emit({ kind: "ready", language: "python", coldStartMs: 1 });
    // No new state delivered after unsubscribe.
    expect(seen.length).toBe(beforeUnsub);
  });

  it("fires ws_token_failed through the per-acquire subscriber when fetchToken rejects", async () => {
    const { factory } = makeFactory();
    const manager = createLSPManager({
      sessionId: "session-1",
      fetchToken: () => Promise.reject(new Error("401")),
      factory,
    });
    const seen: LSPState[] = [];
    const client = await manager.acquire("python", {
      onStateChange: (s) => seen.push(s),
    });
    expect(client).toBeNull();
    // The error state is fanned out to the per-acquire subscriber so
    // the chip can flip to red without polling.
    expect(seen.some((s) => s.kind === "error")).toBe(true);
  });
});
