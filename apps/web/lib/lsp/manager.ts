/**
 * P1-3 — Per-session LSP client manager.
 *
 * Keeps a small LRU map of language → ``LSPClientHandle`` so two files in
 * the same language share a single in-sandbox language server. Opening a
 * third language soft-kills the oldest, matching the design's "max two
 * concurrent LSPs per session" cap.
 *
 * This is a thin wrapper around ``createLSPClient`` — the only stateful
 * job is bookkeeping. The factory function is injected so tests can
 * substitute a stub.
 */
import { createLSPClient, type LSPClientHandle, type LSPLanguage } from "./client";
import { trackLspEvent } from "../telemetry";

export interface LSPManagerOptions {
  sessionId: string;
  /** Resolves a fresh short-lived WS token for ``language``. */
  fetchToken: () => Promise<string>;
  /** Cap on concurrently-attached LSPs. Defaults to 2 per the design. */
  maxClients?: number;
  /** Notified on every client's state transitions. Threaded into createLSPClient. */
  onStateChange?: (
    language: LSPLanguage,
    state: ReturnType<LSPClientHandle["state"]>
  ) => void;
  /** Factory override — tests substitute a stub here. */
  factory?: typeof createLSPClient;
}

/** Returned handle — exposes the same surface as the underlying client. */
export type ManagedLSPClient = LSPClientHandle;

export interface AcquireOptions {
  /**
   * Per-acquire state-change subscriber. The editor footer chip subscribes
   * here so it gets push notifications on every transition (connecting →
   * ready / error / disconnected) instead of polling the client's
   * ``state()`` getter. The manager-level ``onStateChange`` (passed at
   * construction) continues to fire for telemetry / background language
   * surfaces; both run in parallel.
   *
   * Fires once synchronously with the current state on subscribe so the
   * caller doesn't need to read ``client.state()`` separately to seed the
   * UI. Backward-compatible — callers that omit it keep the previous
   * acquire-and-poll behaviour.
   */
  onStateChange?: (state: ReturnType<LSPClientHandle["state"]>) => void;
}

export interface LSPManager {
  /**
   * Acquire (or reuse) a client for ``language``. The token is minted via
   * the manager's ``fetchToken`` on each acquire so the 60 s server-side
   * TTL doesn't bite mid-session. Returns ``null`` if the manager is
   * already disposed.
   *
   * ``opts.onStateChange`` (optional) subscribes to the client's state
   * machine so the consumer can render the LSP chip event-driven; the
   * subscription is auto-detached when the client is closed or evicted.
   */
  acquire(
    language: LSPLanguage,
    opts?: AcquireOptions,
  ): Promise<ManagedLSPClient | null>;
  /** Returns the (in-memory) cached client for ``language``, or null. */
  peek(language: LSPLanguage): ManagedLSPClient | null;
  /** Close one client without disposing the whole manager. */
  release(language: LSPLanguage): void;
  /** Close every client and reject further ``acquire`` calls. */
  dispose(): void;
  /**
   * Detach a per-acquire ``onStateChange`` subscriber registered via
   * ``acquire(lang, { onStateChange })``. Idempotent + safe to call
   * after the underlying client has been evicted.
   */
  unsubscribe(
    language: LSPLanguage,
    cb: (state: ReturnType<LSPClientHandle["state"]>) => void,
  ): void;
}

export function createLSPManager(opts: LSPManagerOptions): LSPManager {
  const {
    sessionId,
    fetchToken,
    maxClients = 2,
    onStateChange,
    factory = createLSPClient,
  } = opts;

  // ``Map`` preserves insertion order, which we abuse for LRU: any
  // ``acquire`` that hits an existing entry re-inserts to bump
  // recency. The eldest key is the LRU eviction target.
  const clients = new Map<LSPLanguage, ManagedLSPClient>();
  let disposed = false;
  // De-dupe concurrent acquires for the same language during the
  // initial token+createLSPClient roundtrip — otherwise CodeEditor's
  // dual mount (StrictMode in dev) double-spawns the WS.
  const inFlight = new Map<LSPLanguage, Promise<ManagedLSPClient | null>>();
  // P1 — per-acquire state-change subscribers. Stored per language so
  // multiple acquires for the same client (re-acquire from a sibling
  // component, etc.) each get their own push-notification stream
  // without re-creating the client. The combined fan-out runs in
  // ``createClient`` below.
  const subscribers = new Map<
    LSPLanguage,
    Set<(state: ReturnType<LSPClientHandle["state"]>) => void>
  >();
  // Last seen state per language — used to seed a newly-attached
  // subscriber synchronously so the consumer doesn't have to call
  // ``client.state()`` separately on subscribe.
  const latestState = new Map<
    LSPLanguage,
    ReturnType<LSPClientHandle["state"]>
  >();

  function touch(language: LSPLanguage, client: ManagedLSPClient): void {
    clients.delete(language);
    clients.set(language, client);
  }

  function evictIfNeeded(): void {
    while (clients.size > maxClients) {
      const oldestKey = clients.keys().next().value as LSPLanguage | undefined;
      if (!oldestKey) break;
      const oldest = clients.get(oldestKey);
      clients.delete(oldestKey);
      try {
        oldest?.close();
      } catch {
        // ignore — best-effort
      }
    }
  }

  function fanOutState(
    language: LSPLanguage,
    state: ReturnType<LSPClientHandle["state"]>,
  ): void {
    latestState.set(language, state);
    try {
      onStateChange?.(language, state);
    } catch {
      // ignore — manager-level subscriber is best-effort
    }
    const subs = subscribers.get(language);
    if (!subs) return;
    for (const cb of subs) {
      try {
        cb(state);
      } catch {
        // ignore — per-acquire subscribers are best-effort
      }
    }
  }

  function ensureSubscribers(language: LSPLanguage): Set<
    (state: ReturnType<LSPClientHandle["state"]>) => void
  > {
    let set = subscribers.get(language);
    if (!set) {
      set = new Set();
      subscribers.set(language, set);
    }
    return set;
  }

  async function acquire(
    language: LSPLanguage,
    opts?: AcquireOptions,
  ): Promise<ManagedLSPClient | null> {
    if (disposed) return null;
    // Per-acquire subscriber. Attached now so the caller doesn't miss any
    // transitions that fire between ``acquire`` resolution and the
    // first React effect tick. We seed it synchronously with the
    // latest known state below (after the client lookup).
    if (opts?.onStateChange) {
      ensureSubscribers(language).add(opts.onStateChange);
    }
    const existing = clients.get(language);
    if (existing) {
      touch(language, existing);
      // Seed the new subscriber with the existing client's state so the
      // chip renders correctly on a re-acquire (e.g. tab switch).
      if (opts?.onStateChange) {
        try {
          opts.onStateChange(latestState.get(language) ?? existing.state());
        } catch {
          // ignore
        }
      }
      return existing;
    }
    const pending = inFlight.get(language);
    if (pending) return pending;
    const promise = (async () => {
      try {
        let token: string;
        try {
          token = await fetchToken();
        } catch {
          // ws-token fetch failed (network blip, auth gone, ...). The
          // LSP is a productivity boost, never load-bearing — but a
          // silent return null leaves the chip hidden, which hides a
          // real failure from the user (and from telemetry). FE-P4
          // audit fix: synthesise the same ``state.kind === "error"``
          // transition the LSP client would emit on an upgrade
          // failure, and fire ``lsp_error`` so the operations
          // dashboard sees the spike. The chip then renders red with
          // the ``ws_token_failed`` error class — same surface as any
          // other initialize / handshake fault.
          fanOutState(language, {
            kind: "error",
            language,
            error: "ws_token_failed",
          });
          try {
            trackLspEvent("lsp_error", {
              language,
              error_class: "ws_token_failed",
            });
          } catch {
            // ignore — telemetry is best-effort
          }
          return null;
        }
        if (disposed) return null;
        const client = factory({
          sessionId,
          language,
          wsToken: token,
          // Single subscriber threaded into createLSPClient; the
          // manager fans out to both the construction-time
          // ``onStateChange`` and every per-acquire subscriber.
          onStateChange: (state) => fanOutState(language, state),
        });
        clients.set(language, client);
        // Seed latest state from whatever the client reports right
        // now (typically "connecting") so a subscriber that attaches
        // mid-handshake gets the chip into the correct color.
        const initial = client.state();
        latestState.set(language, initial);
        if (opts?.onStateChange) {
          try {
            opts.onStateChange(initial);
          } catch {
            // ignore
          }
        }
        evictIfNeeded();
        return client;
      } finally {
        inFlight.delete(language);
      }
    })();
    inFlight.set(language, promise);
    return promise;
  }

  /**
   * Detach a per-acquire subscriber. Exposed via the handle below so
   * the editor's cleanup path can unwire on file/path switch without
   * a memory leak.
   */
  function unsubscribe(
    language: LSPLanguage,
    cb: (state: ReturnType<LSPClientHandle["state"]>) => void,
  ): void {
    subscribers.get(language)?.delete(cb);
  }

  function release(language: LSPLanguage): void {
    const client = clients.get(language);
    if (!client) return;
    clients.delete(language);
    try {
      client.close();
    } catch {
      // ignore
    }
  }

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    for (const [, client] of clients) {
      try {
        client.close();
      } catch {
        // ignore
      }
    }
    clients.clear();
    inFlight.clear();
    subscribers.clear();
    latestState.clear();
  }

  return {
    acquire,
    peek: (language) => clients.get(language) ?? null,
    release,
    dispose,
    unsubscribe,
  };
}

/**
 * Resolve a Monaco language id ↔ LSP language id mapping. Returns
 * ``null`` when the FE Monaco languageId has no matching LSP (e.g.
 * ``markdown``, ``yaml``, ``json``). Per the design note,
 * ``javascript`` maps to the TypeScript server.
 */
export function lspLanguageForMonaco(
  monacoLanguageId: string
): LSPLanguage | null {
  switch (monacoLanguageId) {
    case "python":
      return "python";
    case "typescript":
    case "javascript":
      return "typescript";
    case "go":
      return "go";
    default:
      return null;
  }
}
