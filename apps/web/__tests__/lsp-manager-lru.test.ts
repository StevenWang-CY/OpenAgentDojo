/**
 * FE-P4 audit fix — LSP manager LRU cap regression test.
 *
 * The manager caps concurrently-attached LSP clients at 2 per session
 * (matching the design's "max two languages per workspace" constraint).
 * Opening a third language must close the OLDEST entry, not the
 * just-touched one — otherwise a Python → TypeScript → Go progression
 * would kill the active TypeScript client and leave the chip red.
 *
 * The test injects a stub factory so we never touch a real WebSocket;
 * each stub exposes a ``close`` spy we can assert against.
 */
import { describe, expect, it, vi } from "vitest";
import { createLSPManager } from "@/lib/lsp/manager";
import type { LSPClientHandle, LSPLanguage } from "@/lib/lsp/client";

function makeStubClient(language: LSPLanguage): LSPClientHandle & {
  closeSpy: ReturnType<typeof vi.fn>;
} {
  const closeSpy = vi.fn();
  return {
    language,
    state: () => ({ kind: "ready", language, coldStartMs: 1 }),
    openDocument: () => undefined,
    changeDocument: () => undefined,
    closeDocument: () => undefined,
    requestCompletion: () => Promise.resolve(null),
    requestHover: () => Promise.resolve(null),
    onDiagnostics: () => () => undefined,
    close: closeSpy,
    retry: () => undefined,
    closeSpy,
  } as LSPClientHandle & { closeSpy: ReturnType<typeof vi.fn> };
}

describe("LSPManager — LRU cap (FE-P4)", () => {
  it("evicts the oldest client when a third language is acquired", async () => {
    const handles = new Map<LSPLanguage, ReturnType<typeof makeStubClient>>();
    const factory = vi.fn(
      ({ language }: { language: LSPLanguage }): LSPClientHandle => {
        const stub = makeStubClient(language);
        handles.set(language, stub);
        return stub;
      },
    );
    const manager = createLSPManager({
      sessionId: "session-1",
      fetchToken: () => Promise.resolve("ws-token"),
      factory,
    });

    const py = await manager.acquire("python");
    const ts = await manager.acquire("typescript");
    expect(py).not.toBeNull();
    expect(ts).not.toBeNull();
    // Both still resident — no eviction has fired yet.
    expect(handles.get("python")?.closeSpy).not.toHaveBeenCalled();
    expect(handles.get("typescript")?.closeSpy).not.toHaveBeenCalled();

    // Acquire a third language. The cap is 2, so the oldest entry —
    // Python — must be closed and dropped from the cache.
    const go = await manager.acquire("go");
    expect(go).not.toBeNull();
    expect(handles.get("python")?.closeSpy).toHaveBeenCalledOnce();
    expect(handles.get("typescript")?.closeSpy).not.toHaveBeenCalled();
    expect(handles.get("go")?.closeSpy).not.toHaveBeenCalled();
    // Manager no longer holds the Python entry.
    expect(manager.peek("python")).toBeNull();
    expect(manager.peek("typescript")).not.toBeNull();
    expect(manager.peek("go")).not.toBeNull();
  });

  it("re-acquire of the same language is a no-op (no extra close)", async () => {
    const factory = vi.fn(
      ({ language }: { language: LSPLanguage }): LSPClientHandle =>
        makeStubClient(language),
    );
    const manager = createLSPManager({
      sessionId: "session-1",
      fetchToken: () => Promise.resolve("ws-token"),
      factory,
    });
    const a = await manager.acquire("python");
    const b = await manager.acquire("python");
    expect(a).toBe(b);
    expect(factory).toHaveBeenCalledTimes(1);
  });

  it("returns null when ws-token fetch fails and fires onStateChange + telemetry", async () => {
    const factory = vi.fn(
      ({ language }: { language: LSPLanguage }): LSPClientHandle =>
        makeStubClient(language),
    );
    const states: Array<{ language: LSPLanguage; kind: string }> = [];
    const manager = createLSPManager({
      sessionId: "session-1",
      fetchToken: () => Promise.reject(new Error("ws-token 401")),
      factory,
      onStateChange: (language, state) => {
        states.push({ language, kind: state.kind });
      },
    });
    const client = await manager.acquire("python");
    expect(client).toBeNull();
    // Factory was never called — we bailed before constructing a client.
    expect(factory).not.toHaveBeenCalled();
    // The chip transition that the editor footer renders red is fed
    // by an ``onStateChange`` callback with ``kind: "error"`` and a
    // ``ws_token_failed`` error class.
    expect(states).toContainEqual(
      expect.objectContaining({ language: "python", kind: "error" }),
    );
  });
});
