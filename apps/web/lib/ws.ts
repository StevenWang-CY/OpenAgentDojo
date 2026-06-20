import { env } from "./env";
import { ApiError, getWsToken } from "./api";

/**
 * Auto-reconnecting WebSocket with exponential backoff + heartbeat ping.
 * Used by both the xterm terminal bridge and the supervision-event stream.
 *
 * Backend close-code conventions (mirror `apps/api/app/ws/auth.py`):
 *   - 1008 / 4001 / 4003 / 4404 → fatal (policy / unauthorized / forbidden /
 *                                  session reaped); do not reconnect.
 *   - 4401                       → token expired; re-mint via `getWsToken` then reconnect.
 *   - any other                  → backoff + reconnect, up to `maxAttempts`.
 *
 * 4404 is special-cased: when the backend reaps a session mid-stream, we
 * fire `onSessionEnded` so the caller can render a "session ended" view
 * rather than the generic "exhausted" banner.
 */

export type ReconnectingSocketStatus =
  | "connecting"
  | "open"
  | "closed"
  | "reconnecting"
  | "exhausted";

export interface ReconnectingSocketOptions {
  /** Absolute or path-only URL. Path-only URLs are resolved against env.wsBaseUrl. */
  url: string;
  /** Short-lived auth token; appended as ?token=… if provided and not already present. */
  token?: string;
  protocols?: string | string[];
  /** Max backoff in ms (default 15s). */
  maxBackoffMs?: number;
  /** Initial backoff in ms (default 500ms). */
  initialBackoffMs?: number;
  /** Heartbeat interval in ms; 0 disables (default 25s). */
  heartbeatMs?: number;
  /** Treat this many close codes as fatal — don't reconnect. */
  fatalCloseCodes?: number[];
  /**
   * Hard cap on consecutive reconnect attempts before giving up and emitting
   * `onAttemptsExhausted`. Defaults to 8 per spec §13.
   */
  maxAttempts?: number;
  /**
   * Session id used to re-mint a fresh WS token after a 4401 close. Optional —
   * if omitted, a 4401 close is treated as fatal.
   */
  sessionId?: string;
  /**
   * Resolve fresh query parameters to inject every (re)connect. Returning
   * `{last_id: 42}` causes the next handshake to include `?last_id=42`.
   * Called synchronously on every connect attempt, so it must read live
   * state (e.g. a Zustand store or a ref) — not a value captured at mount.
   */
  resolveQueryParams?: () => Record<string, string | number | undefined>;

  onOpen?: () => void;
  onMessage?: (event: MessageEvent) => void;
  onClose?: (event: CloseEvent) => void;
  onError?: (event: Event) => void;
  onStatusChange?: (status: ReconnectingSocketStatus) => void;
  /** Fired once after `maxAttempts` consecutive failures. */
  onAttemptsExhausted?: () => void;
  /**
   * Fired when a token-refresh hits 401 — i.e. the cookie session has
   * expired. The caller should redirect to sign-in; we do *not* keep
   * looping the WS with a stale token. Once invoked, the socket transitions
   * to "exhausted" and no further reconnects are scheduled.
   */
  onAuthFailure?: () => void;
  /**
   * Fired exactly once when the backend closes the socket with code 4404
   * (session reaped). The socket transitions to "closed" and no further
   * reconnects are scheduled. Callers can use this to render a terminal
   * "session ended" view rather than a misleading "reconnecting…" banner.
   */
  onSessionEnded?: () => void;
}

export interface ReconnectingSocketHandle {
  // Mirror the native ``WebSocket.send`` parameter type. TS 6 narrowed it to
  // ``BufferSource | Blob | string`` (where ``BufferSource`` is backed by a
  // concrete ``ArrayBuffer``), dropping the ``SharedArrayBuffer`` arm that
  // ``ArrayBufferLike`` would otherwise admit.
  send(data: string | ArrayBuffer | ArrayBufferView<ArrayBuffer> | Blob): void;
  close(): void;
  status(): ReconnectingSocketStatus;
}

/**
 * Build an absolute ws/wss URL from a path or pass-through an absolute URL.
 * Extra params (e.g. `{last_id: 42}`) are appended verbatim — `undefined`
 * values are skipped so callers can pass sparse param maps.
 */
export function buildWsUrl(
  pathOrUrl: string,
  token?: string,
  extras?: Record<string, string | number | undefined>
): string {
  const base = /^wss?:\/\//i.test(pathOrUrl) ? pathOrUrl : `${env.wsBaseUrl}${pathOrUrl}`;
  if (!token && !extras) return base;
  const url = new URL(base);
  if (token) url.searchParams.set("token", token);
  if (extras) {
    for (const [k, v] of Object.entries(extras)) {
      if (v === undefined) continue;
      url.searchParams.set(k, String(v));
    }
  }
  return url.toString();
}

/**
 * Strip an existing `?token=` query parameter so we can re-mint cleanly.
 * Volatile params (e.g. `last_id`) are also stripped — they're re-injected
 * on every connect via `resolveQueryParams`.
 */
function urlWithoutToken(pathOrUrl: string): string {
  const base = /^wss?:\/\//i.test(pathOrUrl) ? pathOrUrl : `${env.wsBaseUrl}${pathOrUrl}`;
  try {
    const u = new URL(base);
    u.searchParams.delete("token");
    u.searchParams.delete("last_id");
    return u.toString();
  } catch {
    return base;
  }
}

export function createReconnectingSocket(
  opts: ReconnectingSocketOptions
): ReconnectingSocketHandle {
  const {
    url: rawUrl,
    token,
    protocols,
    maxBackoffMs = 15_000,
    initialBackoffMs = 500,
    heartbeatMs = 25_000,
    fatalCloseCodes = [1008, 4001, 4003, 4404],
    maxAttempts = 8,
    sessionId,
    resolveQueryParams,
    onOpen,
    onMessage,
    onClose,
    onError,
    onStatusChange,
    onAttemptsExhausted,
    onAuthFailure,
    onSessionEnded,
  } = opts;

  const baseUrlNoToken = urlWithoutToken(rawUrl);
  let currentToken: string | undefined = token;

  let socket: WebSocket | null = null;
  let attempt = 0;
  let closedByUser = false;
  let exhausted = false;
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let currentStatus: ReconnectingSocketStatus = "connecting";

  function setStatus(next: ReconnectingSocketStatus): void {
    if (currentStatus === next) return;
    currentStatus = next;
    onStatusChange?.(next);
  }

  function clearTimers(): void {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function startHeartbeat(): void {
    if (heartbeatMs <= 0) return;
    heartbeatTimer = setInterval(() => {
      if (socket?.readyState === WebSocket.OPEN) {
        try {
          socket.send(JSON.stringify({ type: "ping", ts: Date.now() }));
        } catch {
          // ignore; the next close handler will reconnect
        }
      }
    }, heartbeatMs);
  }

  function backoff(): number {
    const delay = Math.min(maxBackoffMs, initialBackoffMs * 2 ** attempt);
    // Full jitter — recommended for thundering herds.
    return Math.round(Math.random() * delay);
  }

  function connect(): void {
    if (exhausted || closedByUser) return;
    setStatus(attempt === 0 ? "connecting" : "reconnecting");

    // Resolve extras *every* connect so reconnects pick up the latest
    // `last_id` from the live store without us having to re-create the
    // socket factory on every state change.
    const extras = resolveQueryParams?.() ?? {};
    const url = buildWsUrl(baseUrlNoToken, currentToken, extras);

    try {
      socket = new WebSocket(url, protocols);
    } catch {
      scheduleReconnect();
      return;
    }

    socket.addEventListener("open", () => {
      attempt = 0;
      setStatus("open");
      startHeartbeat();
      onOpen?.();
    });

    socket.addEventListener("message", (event) => {
      onMessage?.(event);
    });

    socket.addEventListener("error", (event) => {
      onError?.(event);
    });

    socket.addEventListener("close", (event) => {
      clearTimers();
      onClose?.(event);
      if (closedByUser) {
        setStatus("closed");
        return;
      }
      if (fatalCloseCodes.includes(event.code)) {
        // 4404 ⇒ the session has been reaped on the backend. There's no
        // value in spamming reconnects; fire the dedicated callback so the
        // caller can switch to a terminal "session ended" view.
        if (event.code === 4404) {
          exhausted = true;
          onSessionEnded?.();
        }
        setStatus("closed");
        return;
      }
      // Token expired — try to mint a fresh one before reconnecting.
      if (event.code === 4401 && sessionId) {
        attempt += 1;
        if (attempt > maxAttempts) {
          exhausted = true;
          setStatus("exhausted");
          onAttemptsExhausted?.();
          return;
        }
        setStatus("reconnecting");
        getWsToken(sessionId)
          .then((resp) => {
            currentToken = resp.token;
            reconnectTimer = setTimeout(connect, backoff());
          })
          .catch((err: unknown) => {
            // A 401 from the token endpoint means the user's cookie session
            // has expired — looping with the old WS token would only burn
            // reconnect attempts. Bail out and let the caller redirect to
            // sign-in.
            if (err instanceof ApiError && err.status === 401) {
              exhausted = true;
              setStatus("exhausted");
              onAuthFailure?.();
              return;
            }
            // Anything else (network blip, 5xx) is unexpected on the token
            // endpoint. Log it for visibility and let the standard backoff
            // path take over — but funnel through scheduleReconnect so
            // maxAttempts is honoured and we don't quietly loop forever.
            console.error(
              "[ws] token refresh failed; falling back to reconnect",
              err
            );
            scheduleReconnect();
          });
        return;
      }
      scheduleReconnect();
    });
  }

  function scheduleReconnect(): void {
    attempt += 1;
    if (attempt > maxAttempts) {
      exhausted = true;
      setStatus("exhausted");
      onAttemptsExhausted?.();
      return;
    }
    setStatus("reconnecting");
    reconnectTimer = setTimeout(connect, backoff());
  }

  connect();

  return {
    send(data) {
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(data);
      }
      // Silently drop while reconnecting — callers can re-send on `onOpen`.
    },
    close() {
      closedByUser = true;
      clearTimers();
      socket?.close(1000, "client_closed");
      setStatus("closed");
    },
    status() {
      return currentStatus;
    },
  };
}
