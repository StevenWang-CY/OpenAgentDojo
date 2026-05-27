/**
 * P1-3 — LSP client for Monaco.
 *
 * Hand-rolled, narrow-scope JSON-RPC over WebSocket adapter that wires the
 * sandbox-spawned language server (pyright / typescript-language-server /
 * gopls) into the existing ``@monaco-editor/react`` setup. We don't pull
 * in ``monaco-languageclient`` v10 here because v10 mandates the
 * ``@codingame/monaco-vscode-api`` runtime, which would swap out the
 * editor wrapper this codebase already uses. The adapter below covers the
 * three load-bearing surfaces the design calls for — completion, hover,
 * publishDiagnostics — and stops there.
 *
 * Protocol contract (mirrors apps/api/app/ws/lsp.py):
 *   - URL: ``ws://.../ws/sessions/{sessionId}/lsp?language=<lang>&token=<jwt>``
 *     (path matches what ``lsp_ws_router`` is mounted at in ``main.py`` —
 *     i.e. NOT under the ``/api/v1`` prefix; see the include_router for
 *     terminal/events/lsp WS routers.)
 *   - Subprotocol: ``lsp.openagentdojo.v1`` (must echo from server).
 *   - First server frame may be a structured ``lsp_error`` text frame.
 *     We detect it and bail BEFORE entering the JSON-RPC pump.
 *   - After that the channel is byte-for-byte LSP JSON-RPC framed with
 *     ``Content-Length: <n>\r\n\r\n<body>`` per the LSP spec.
 *
 * Lifecycle:
 *   - ``createLSPClient`` opens the socket, runs ``initialize`` /
 *     ``initialized`` against the server, sets the languageclient
 *     "ready" so providers can fire, and returns a handle.
 *   - The handle exposes:
 *       * ``openDocument(uri, content)`` → ``textDocument/didOpen``
 *       * ``changeDocument(uri, content, version)`` → ``textDocument/didChange``
 *       * ``closeDocument(uri)`` → ``textDocument/didClose``
 *       * ``requestCompletion(uri, position)`` → completion list
 *       * ``requestHover(uri, position)`` → hover info
 *       * ``onDiagnostics(cb)`` → register a listener for publishDiagnostics
 *       * ``close()`` / ``retry()`` lifecycle controls
 *   - State is reported via ``onStateChange`` so the editor footer chip can
 *     reflect connecting → ready → error.
 *
 * The WebSocket constructor is injected (``wsFactory``) so the unit test
 * can stub it without depending on a real socket — see
 * ``__tests__/lsp-client.test.ts``.
 */
import { env } from "../env";
import { trackLspEvent } from "../telemetry";
import { encodeFrame, FrameDecoder, type JsonRpcMessage } from "./framing";

/** Languages the backend LSP proxy knows how to spawn. */
export const LSP_SUPPORTED_LANGUAGES = ["python", "typescript", "go"] as const;
export type LSPLanguage = (typeof LSP_SUPPORTED_LANGUAGES)[number];

/** Subprotocol the backend negotiates on upgrade. */
export const LSP_SUBPROTOCOL = "lsp.openagentdojo.v1";

/** Cold-start grace window for the amber chip — anything longer is suspect. */
export const LSP_COLD_START_MS = 5_000;
/**
 * Hard timeout on the JSON-RPC ``initialize`` handshake. FE-P4 audit fix
 * — previously the cold-start budget was telemetry-only, which left the
 * chip stuck on amber forever when an LSP never responded (we'd see it
 * in logs but the user wouldn't get a red state to retry from). At
 * ``LSP_COLD_START_TIMEOUT_MS`` we force the state machine into
 * ``error`` with ``error: "initialize_timeout"`` and close the WS so
 * the consumer can call ``retry()``.
 */
export const LSP_COLD_START_TIMEOUT_MS = 15_000;

/** Mirrors ``LSPErrorFrame`` on the backend. */
export interface LSPErrorFrame {
  type: "lsp_error";
  error:
    | "binary_not_found"
    | "spawn_failed"
    | "unsupported_language"
    | "driver_unavailable"
    | "lsp_already_running"
    | "session_not_active"
    | "session_not_found"
    | "no_sandbox"
    | string;
  language: string;
  detail?: string | null;
}

export type LSPState =
  | { kind: "connecting"; language: LSPLanguage }
  | { kind: "ready"; language: LSPLanguage; coldStartMs: number }
  | {
      kind: "error";
      language: LSPLanguage;
      error: string;
      detail?: string | null;
    }
  | { kind: "disconnected"; language: LSPLanguage; closeCode?: number };

/** LSP ``Diagnostic`` — only the fields Monaco needs. */
export interface LSPDiagnostic {
  range: {
    start: { line: number; character: number };
    end: { line: number; character: number };
  };
  severity?: 1 | 2 | 3 | 4;
  code?: string | number;
  source?: string;
  message: string;
}

export interface PublishDiagnosticsParams {
  uri: string;
  diagnostics: LSPDiagnostic[];
}

export interface CompletionItem {
  label: string;
  kind?: number;
  detail?: string;
  documentation?: string | { kind: string; value: string };
  insertText?: string;
  filterText?: string;
  sortText?: string;
}

export interface CompletionList {
  isIncomplete?: boolean;
  items: CompletionItem[];
}

export interface HoverContent {
  contents: unknown;
  range?: LSPDiagnostic["range"];
}

export interface LSPClientHandle {
  readonly language: LSPLanguage;
  state(): LSPState;
  openDocument(uri: string, content: string, languageId: string): void;
  changeDocument(uri: string, content: string, version: number): void;
  closeDocument(uri: string): void;
  requestCompletion(
    uri: string,
    position: { line: number; character: number },
    signal?: AbortSignal
  ): Promise<CompletionList | CompletionItem[] | null>;
  requestHover(
    uri: string,
    position: { line: number; character: number },
    signal?: AbortSignal
  ): Promise<HoverContent | null>;
  onDiagnostics(cb: (params: PublishDiagnosticsParams) => void): () => void;
  close(): void;
  retry(): void;
}

export interface CreateLSPClientOptions {
  sessionId: string;
  language: LSPLanguage;
  /** Short-lived WS auth token; the backend HMAC-verifies on upgrade. */
  wsToken: string;
  /** Workspace root URI (e.g. ``file:///workspace``). Defaults to that. */
  rootUri?: string;
  /** Notified on every state transition. */
  onStateChange?: (state: LSPState) => void;
  /**
   * Override the WebSocket constructor — used by tests to inject a stub.
   * Defaults to ``globalThis.WebSocket``.
   */
  wsFactory?: (url: string, protocols?: string | string[]) => WebSocket;
  /** Optional override for the ws base URL (default: ``env.wsBaseUrl``). */
  wsBaseUrl?: string;
  /** Optional logger hook for diagnostics during dev. */
  logger?: { debug: (...a: unknown[]) => void; warn: (...a: unknown[]) => void };
}

/** Build the LSP WS URL with all required params. */
export function buildLspUrl(
  sessionId: string,
  language: LSPLanguage,
  token: string,
  wsBaseUrl: string = env.wsBaseUrl
): string {
  const base = `${wsBaseUrl}/ws/sessions/${encodeURIComponent(
    sessionId
  )}/lsp`;
  const url = new URL(base);
  url.searchParams.set("language", language);
  url.searchParams.set("token", token);
  return url.toString();
}

const DEFAULT_LOGGER = {
  debug: (..._args: unknown[]) => undefined,
  warn: (...args: unknown[]) => {
    if (typeof console !== "undefined") {
      console.warn("[lsp]", ...args);
    }
  },
};

/**
 * Create a connected LSP client. Returns synchronously with a handle —
 * the initialize handshake runs in the background and state transitions
 * are reported via ``onStateChange``.
 */
export function createLSPClient(opts: CreateLSPClientOptions): LSPClientHandle {
  const {
    sessionId,
    language,
    wsToken,
    rootUri = "file:///workspace",
    onStateChange,
    wsFactory,
    wsBaseUrl,
    logger = DEFAULT_LOGGER,
  } = opts;

  if (!LSP_SUPPORTED_LANGUAGES.includes(language)) {
    throw new Error(`unsupported LSP language: ${String(language)}`);
  }

  const factory: (url: string, protocols?: string | string[]) => WebSocket =
    wsFactory ??
    ((url, protocols) =>
      protocols !== undefined
        ? new WebSocket(url, protocols)
        : new WebSocket(url));

  let socket: WebSocket | null = null;
  let state: LSPState = { kind: "connecting", language };
  let nextId = 1;
  let openedAt = 0;
  let closedByUser = false;
  let initialised = false;
  let coldStartTimer: ReturnType<typeof setTimeout> | undefined;
  // FE-P4 audit fix — separate hard-deadline timer so we can flip
  // state to ``error: initialize_timeout`` if the handshake never
  // resolves. Cleared once ``runInitializeHandshake`` settles
  // (either to ``ready`` or ``error``) or on a user-driven close.
  let initializeTimeoutTimer: ReturnType<typeof setTimeout> | undefined;

  const decoder = new FrameDecoder();
  const pendingRequests = new Map<
    number,
    { resolve: (v: unknown) => void; reject: (e: Error) => void }
  >();
  const diagnosticsListeners = new Set<
    (params: PublishDiagnosticsParams) => void
  >();
  const openedDocs = new Map<string, { version: number; languageId: string }>();
  // Queued didOpen notifications that arrived before the initialize
  // handshake completed. Flushed by ``flushPendingDidOpen`` once the
  // server responds to ``initialize``.
  const pendingDidOpen: { uri: string; content: string; languageId: string }[] =
    [];

  function setState(next: LSPState): void {
    state = next;
    try {
      onStateChange?.(next);
    } catch (err) {
      logger.warn("onStateChange threw:", err);
    }
  }

  function sendFrame(msg: JsonRpcMessage): void {
    if (!socket || socket.readyState !== 1 /* OPEN */) {
      logger.debug("dropping frame; socket not open", msg.method ?? msg.id);
      return;
    }
    try {
      socket.send(encodeFrame(msg));
    } catch (err) {
      logger.warn("send failed:", err);
    }
  }

  function sendNotification(method: string, params: unknown): void {
    sendFrame({ jsonrpc: "2.0", method, params });
  }

  function sendRequest<T = unknown>(
    method: string,
    params: unknown,
    signal?: AbortSignal
  ): Promise<T> {
    const id = nextId++;
    return new Promise<T>((resolve, reject) => {
      pendingRequests.set(id, {
        resolve: (v) => resolve(v as T),
        reject,
      });
      if (signal) {
        const onAbort = (): void => {
          pendingRequests.delete(id);
          reject(new DOMException("Aborted", "AbortError"));
        };
        if (signal.aborted) {
          onAbort();
          return;
        }
        signal.addEventListener("abort", onAbort, { once: true });
      }
      sendFrame({ jsonrpc: "2.0", id, method, params });
    });
  }

  function handleMessage(msg: JsonRpcMessage): void {
    if (msg.id !== undefined && msg.id !== null && msg.method === undefined) {
      // Response to one of our requests.
      const pending = pendingRequests.get(msg.id as number);
      if (!pending) return;
      pendingRequests.delete(msg.id as number);
      if (msg.error) {
        pending.reject(
          new Error(`LSP error ${msg.error.code}: ${msg.error.message}`)
        );
        return;
      }
      pending.resolve(msg.result);
      return;
    }
    if (!msg.method) return;
    // Notification or request from server. We service publishDiagnostics
    // and ignore everything else; LSP-spec workspace/configuration etc.
    // are not load-bearing for the editor experience and a missing
    // response just makes the server log a complaint.
    switch (msg.method) {
      case "textDocument/publishDiagnostics": {
        const params = msg.params as PublishDiagnosticsParams | undefined;
        if (params && Array.isArray(params.diagnostics)) {
          for (const cb of diagnosticsListeners) {
            try {
              cb(params);
            } catch (err) {
              logger.warn("diagnostics listener threw:", err);
            }
          }
        }
        return;
      }
      case "window/logMessage":
      case "window/showMessage":
      case "$/progress":
      case "window/workDoneProgress/create":
      case "telemetry/event":
        return;
      default:
        // Some servers send a request expecting a response (workspace/
        // configuration, client/registerCapability, ...). Reply with a
        // permissive null so the server doesn't block. This is the
        // narrow "good enough" path; a full languageclient would
        // negotiate properly.
        if (msg.id !== undefined && msg.id !== null) {
          sendFrame({ jsonrpc: "2.0", id: msg.id, result: null });
        }
        return;
    }
  }

  function clearInitializeTimeout(): void {
    if (initializeTimeoutTimer !== undefined) {
      clearTimeout(initializeTimeoutTimer);
      initializeTimeoutTimer = undefined;
    }
  }

  async function runInitializeHandshake(): Promise<void> {
    try {
      await sendRequest("initialize", {
        processId: null,
        clientInfo: { name: "openagentdojo-web", version: "1" },
        rootUri,
        capabilities: {
          textDocument: {
            synchronization: {
              dynamicRegistration: false,
              willSave: false,
              willSaveWaitUntil: false,
              didSave: false,
            },
            completion: {
              completionItem: {
                snippetSupport: false,
                documentationFormat: ["markdown", "plaintext"],
              },
            },
            hover: { contentFormat: ["markdown", "plaintext"] },
            publishDiagnostics: { relatedInformation: false },
          },
          workspace: {
            workspaceFolders: true,
            configuration: false,
          },
        },
        workspaceFolders: [{ uri: rootUri, name: "workspace" }],
      });
      sendNotification("initialized", {});
      initialised = true;
      clearInitializeTimeout();
      const coldStartMs = Date.now() - openedAt;
      flushPendingDidOpen();
      setState({ kind: "ready", language, coldStartMs });
      try {
        trackLspEvent("lsp_session_started", {
          language,
          cold_start_ms: coldStartMs,
        });
      } catch (err) {
        logger.debug("telemetry trackLspEvent failed:", err);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      logger.warn("initialize failed:", message);
      clearInitializeTimeout();
      // Only surface ``initialize_failed`` if the timeout watchdog
      // hasn't already flipped state to ``initialize_timeout``. The
      // sendRequest rejection arrives moments after we close() the
      // socket from the watchdog, and we don't want to clobber the
      // more-actionable error class.
      if (state.kind !== "error") {
        setState({
          kind: "error",
          language,
          error: "initialize_failed",
          detail: message,
        });
        try {
          trackLspEvent("lsp_error", { language, error_class: "initialize_failed" });
        } catch {
          // ignore
        }
      }
    }
  }

  function connect(): void {
    closedByUser = false;
    initialised = false;
    setState({ kind: "connecting", language });
    const url = buildLspUrl(sessionId, language, wsToken, wsBaseUrl);
    let ws: WebSocket;
    try {
      ws = factory(url, [LSP_SUBPROTOCOL]);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setState({
        kind: "error",
        language,
        error: "ws_construct_failed",
        detail: message,
      });
      return;
    }
    socket = ws;
    openedAt = Date.now();
    // ``binaryType`` defaults to "blob"; we need arraybuffer for the
    // synchronous Uint8Array path in ``onmessage``.
    try {
      ws.binaryType = "arraybuffer";
    } catch {
      // ignore — only fails if assigned after open in some old browsers
    }

    if (coldStartTimer !== undefined) clearTimeout(coldStartTimer);
    coldStartTimer = setTimeout(() => {
      // The amber chip lives in the editor footer; the consumer reads
      // ``state.kind === "connecting"`` for it. We don't transition
      // state here — the chip is allowed to stay "connecting" past
      // the 5 s budget; it just reads as red-amber once the consumer
      // does the math. We do, however, log so telemetry surfaces a
      // suspicious cold start.
      if (!initialised) {
        logger.debug(
          `[lsp:${language}] cold start exceeded ${LSP_COLD_START_MS}ms`
        );
      }
    }, LSP_COLD_START_MS);
    // FE-P4 audit fix — hard cap on the initialize handshake. If
    // ``initialize`` doesn't respond by ``LSP_COLD_START_TIMEOUT_MS``,
    // flip the state machine into ``error: initialize_timeout``, fire
    // ``lsp_error`` telemetry, and tear down the WS so a follow-up
    // ``retry()`` can spin a fresh attempt instead of leaving the
    // chip pinned on amber forever.
    if (initializeTimeoutTimer !== undefined) clearTimeout(initializeTimeoutTimer);
    initializeTimeoutTimer = setTimeout(() => {
      if (initialised) return;
      if (state.kind === "error") return;
      setState({
        kind: "error",
        language,
        error: "initialize_timeout",
        detail: `initialize did not respond within ${LSP_COLD_START_TIMEOUT_MS}ms`,
      });
      try {
        trackLspEvent("lsp_error", {
          language,
          error_class: "initialize_timeout",
        });
      } catch {
        // ignore — telemetry is best-effort
      }
      // Reject any pending requests so the language client doesn't
      // hang on the promise tail. The onclose handler below would
      // already do this, but we beat it to the punch so the consumer
      // sees the error state before the socket fully closes.
      for (const [id, p] of pendingRequests) {
        p.reject(new Error("LSP initialize timeout"));
        pendingRequests.delete(id);
      }
      try {
        socket?.close();
      } catch {
        // ignore — best-effort teardown
      }
    }, LSP_COLD_START_TIMEOUT_MS);

    ws.onopen = () => {
      // Kick off initialize once the socket is fully open.
      void runInitializeHandshake();
    };
    ws.onmessage = (ev) => {
      // Backend sends either:
      //   (a) a single text frame containing an ``lsp_error`` JSON object,
      //       used to abort the channel BEFORE entering the JSON-RPC pump
      //   (b) binary frames carrying byte-for-byte LSP JSON-RPC stdout.
      if (typeof ev.data === "string") {
        let parsed: LSPErrorFrame | null = null;
        try {
          parsed = JSON.parse(ev.data) as LSPErrorFrame;
        } catch {
          parsed = null;
        }
        if (parsed && parsed.type === "lsp_error") {
          setState({
            kind: "error",
            language,
            error: parsed.error,
            detail: parsed.detail ?? null,
          });
          try {
            trackLspEvent("lsp_error", {
              language,
              error_class: parsed.error,
            });
          } catch {
            // ignore
          }
          // The backend will close the socket on the heels of this frame
          // — we don't have to ws.close() ourselves; the onclose handler
          // below leaves state as "error" because ``initialised`` is
          // still false and ``state.kind`` is already "error".
          return;
        }
        // Server might emit text frames carrying LSP bytes too (some
        // servers stringify). Feed them through the decoder verbatim.
        decoder.push(new TextEncoder().encode(ev.data));
      } else if (ev.data instanceof ArrayBuffer) {
        decoder.push(new Uint8Array(ev.data));
      } else if (ev.data instanceof Blob) {
        void ev.data.arrayBuffer().then((buf) => {
          decoder.push(new Uint8Array(buf));
          for (const m of decoder.drain()) handleMessage(m);
        });
        return;
      } else if (ev.data && ArrayBuffer.isView(ev.data)) {
        const view = ev.data as ArrayBufferView;
        decoder.push(
          new Uint8Array(view.buffer, view.byteOffset, view.byteLength)
        );
      } else {
        return;
      }
      for (const m of decoder.drain()) handleMessage(m);
    };
    ws.onerror = () => {
      logger.debug(`[lsp:${language}] ws error`);
    };
    ws.onclose = (ev) => {
      if (coldStartTimer !== undefined) {
        clearTimeout(coldStartTimer);
        coldStartTimer = undefined;
      }
      clearInitializeTimeout();
      // If we already saw a structured ``lsp_error`` frame, leave the
      // state as-is so the chip stays red with the correct error class.
      if (state.kind === "error") return;
      if (closedByUser) {
        setState({ kind: "disconnected", language, closeCode: ev.code });
        return;
      }
      // Unexpected close — surface as disconnected. The consumer can
      // call ``retry()`` to re-open.
      setState({ kind: "disconnected", language, closeCode: ev.code });
      try {
        trackLspEvent("lsp_error", {
          language,
          error_class: `close_${ev.code || "unknown"}`,
        });
      } catch {
        // ignore
      }
      // Reject all in-flight requests so awaiting callers don't hang.
      for (const [id, p] of pendingRequests) {
        p.reject(new Error(`LSP socket closed (code ${ev.code})`));
        pendingRequests.delete(id);
      }
    };
  }

  function flushPendingDidOpen(): void {
    if (!initialised) return;
    while (pendingDidOpen.length > 0) {
      const item = pendingDidOpen.shift()!;
      sendNotification("textDocument/didOpen", {
        textDocument: {
          uri: item.uri,
          languageId: item.languageId,
          version: 1,
          text: item.content,
        },
      });
    }
  }

  connect();

  const handle: LSPClientHandle = {
    language,
    state: () => state,
    openDocument(uri, content, languageId) {
      openedDocs.set(uri, { version: 1, languageId });
      if (!initialised) {
        // Stash for after initialize. ``flushPendingDidOpen`` runs at
        // the tail of ``runInitializeHandshake`` once the server
        // responds, so the FE can call ``openDocument`` immediately on
        // ``createLSPClient`` without racing the handshake.
        pendingDidOpen.push({ uri, content, languageId });
        return;
      }
      sendNotification("textDocument/didOpen", {
        textDocument: {
          uri,
          languageId,
          version: 1,
          text: content,
        },
      });
    },
    changeDocument(uri, content, version) {
      const doc = openedDocs.get(uri);
      if (doc) doc.version = version;
      if (!initialised) return;
      sendNotification("textDocument/didChange", {
        textDocument: { uri, version },
        // Full-document sync — the simpler path. Range-based sync
        // would be faster on huge files but the workspace LSPs all
        // negotiate either mode.
        contentChanges: [{ text: content }],
      });
    },
    closeDocument(uri) {
      openedDocs.delete(uri);
      if (!initialised) return;
      sendNotification("textDocument/didClose", {
        textDocument: { uri },
      });
    },
    requestCompletion(uri, position, signal) {
      if (!initialised) return Promise.resolve(null);
      return sendRequest<CompletionList | CompletionItem[] | null>(
        "textDocument/completion",
        { textDocument: { uri }, position },
        signal
      ).catch((err) => {
        logger.debug("completion failed:", err);
        return null;
      });
    },
    requestHover(uri, position, signal) {
      if (!initialised) return Promise.resolve(null);
      return sendRequest<HoverContent | null>(
        "textDocument/hover",
        { textDocument: { uri }, position },
        signal
      ).catch((err) => {
        logger.debug("hover failed:", err);
        return null;
      });
    },
    onDiagnostics(cb) {
      diagnosticsListeners.add(cb);
      return () => {
        diagnosticsListeners.delete(cb);
      };
    },
    close() {
      closedByUser = true;
      if (coldStartTimer !== undefined) {
        clearTimeout(coldStartTimer);
        coldStartTimer = undefined;
      }
      clearInitializeTimeout();
      // Best-effort polite shutdown: only if we got past initialize.
      if (initialised) {
        try {
          sendRequest("shutdown", null).catch(() => undefined);
          sendNotification("exit", null);
        } catch {
          // ignore
        }
      }
      try {
        socket?.close();
      } catch {
        // ignore
      }
      pendingRequests.clear();
      diagnosticsListeners.clear();
      openedDocs.clear();
      pendingDidOpen.length = 0;
    },
    retry() {
      try {
        socket?.close();
      } catch {
        // ignore
      }
      socket = null;
      decoder.push(new Uint8Array(0)); // no-op, but documents intent
      connect();
    },
  };

  return handle;
}
