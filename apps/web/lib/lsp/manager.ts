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

export interface LSPManager {
  /**
   * Acquire (or reuse) a client for ``language``. The token is minted via
   * the manager's ``fetchToken`` on each acquire so the 60 s server-side
   * TTL doesn't bite mid-session. Returns ``null`` if the manager is
   * already disposed.
   */
  acquire(language: LSPLanguage): Promise<ManagedLSPClient | null>;
  /** Returns the (in-memory) cached client for ``language``, or null. */
  peek(language: LSPLanguage): ManagedLSPClient | null;
  /** Close one client without disposing the whole manager. */
  release(language: LSPLanguage): void;
  /** Close every client and reject further ``acquire`` calls. */
  dispose(): void;
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

  async function acquire(
    language: LSPLanguage
  ): Promise<ManagedLSPClient | null> {
    if (disposed) return null;
    const existing = clients.get(language);
    if (existing) {
      touch(language, existing);
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
          try {
            onStateChange?.(language, {
              kind: "error",
              language,
              error: "ws_token_failed",
            });
          } catch {
            // ignore — onStateChange is best-effort
          }
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
          onStateChange: onStateChange
            ? (state) => onStateChange(language, state)
            : undefined,
        });
        clients.set(language, client);
        evictIfNeeded();
        return client;
      } finally {
        inFlight.delete(language);
      }
    })();
    inFlight.set(language, promise);
    return promise;
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
  }

  return {
    acquire,
    peek: (language) => clients.get(language) ?? null,
    release,
    dispose,
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
