"use client";

import * as React from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useTheme } from "@/stores/themeStore";
import {
  createReconnectingSocket,
  type ReconnectingSocketHandle,
  type ReconnectingSocketStatus,
} from "@/lib/ws";
import { getWsToken } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface TerminalProps {
  /** Session id — wires the xterm to /ws/sessions/{id}/terminal. */
  sessionId: string;
  /**
   * Optional initial short-lived WS auth token (e.g. the one returned with
   * `GET /sessions/{id}`). If omitted, the terminal mints one itself via
   * `getWsToken(sessionId)`. Either way, a fresh token is minted on every
   * (re)mount so the 60s server-side TTL doesn't bite during long sessions.
   */
  token?: string;
  className?: string;
}

type XtermModule = typeof import("xterm");
type FitAddonModule = typeof import("xterm-addon-fit");
type WebLinksAddonModule = typeof import("xterm-addon-web-links");

type TerminalInstance = InstanceType<XtermModule["Terminal"]>;
type FitAddonInstance = InstanceType<FitAddonModule["FitAddon"]>;

/**
 * xterm.js bound to the backend PTY over WebSocket. Mounted lazily — xterm
 * touches `document` on import so it can't ship in the server bundle.
 */
export function Terminal({ sessionId, token, className }: TerminalProps) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const { resolvedTheme } = useTheme();

  const [status, setStatus] = React.useState<ReconnectingSocketStatus>("connecting");
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let disposed = false;
    let socket: ReconnectingSocketHandle | null = null;
    let term: TerminalInstance | null = null;
    let onResize: (() => void) | null = null;

    // Buffer keystrokes + resize control frames that the user produces while
    // the socket is in `reconnecting` / `closed`. We bound the buffer by
    // bytes so a runaway hold-down-a-key can't pin a tab in memory; once
    // full, we drop newest frames and surface a single toast so the user
    // knows their typing is no longer reaching the PTY.
    const PENDING_BUFFER_BYTES = 64 * 1024;
    const pendingFrames: (string | Uint8Array)[] = [];
    let pendingBytes = 0;
    let warnedBufferFull = false;

    function frameSize(frame: string | Uint8Array): number {
      // String → UTF-8 byte length is the worst-case payload for `send`.
      if (typeof frame === "string") {
        // Cheap upper bound — every char is at most 4 UTF-8 bytes.
        return frame.length * 4;
      }
      return frame.byteLength;
    }

    function bufferFrame(frame: string | Uint8Array): void {
      const size = frameSize(frame);
      if (pendingBytes + size > PENDING_BUFFER_BYTES) {
        if (!warnedBufferFull) {
          warnedBufferFull = true;
          toast.warning("Buffer full, releasing keys");
        }
        return;
      }
      pendingFrames.push(frame);
      pendingBytes += size;
    }

    function flushPending(activeSocket: ReconnectingSocketHandle): void {
      if (pendingFrames.length === 0) return;
      while (pendingFrames.length > 0) {
        const next = pendingFrames.shift()!;
        try {
          activeSocket.send(next);
        } catch {
          // If a send fails mid-flush, drop it — the underlying socket will
          // re-trigger a reconnect on its own.
        }
      }
      pendingBytes = 0;
      warnedBufferFull = false;
    }

    // Track WS status transitions so we can surface a single user-facing
    // toast on the *first* reconnect attempt, and a confirming toast once we
    // come back online after a drop. Initial connect/open is silent.
    let lastStatus: ReconnectingSocketStatus = "connecting";
    let everReconnected = false;

    function handleStatusChange(next: ReconnectingSocketStatus): void {
      if (next === lastStatus) return;
      if (next === "reconnecting" && !everReconnected) {
        everReconnected = true;
        toast("Terminal reconnecting…");
      } else if (next === "open" && everReconnected) {
        toast.success("Terminal connected");
        if (socket) flushPending(socket);
      }
      lastStatus = next;
      setStatus(next);
    }

    async function boot() {
      try {
        // Mint a fresh short-lived WS token. Prefer the bootstrap `token`
        // (if the parent already had one in SessionDetail) to skip the
        // first roundtrip; otherwise hit /sessions/{id}/ws-token directly.
        const tokenPromise: Promise<string> = token
          ? Promise.resolve(token)
          : getWsToken(sessionId).then((r) => r.token);

        const [xtermMod, fitMod, linksMod, wsToken]: [
          XtermModule,
          FitAddonModule,
          WebLinksAddonModule,
          string,
        ] = await Promise.all([
          import("xterm"),
          import("xterm-addon-fit"),
          import("xterm-addon-web-links"),
          tokenPromise,
        ]);
        await import("xterm/css/xterm.css");

        if (disposed || !containerRef.current) return;

        const t: TerminalInstance = new xtermMod.Terminal({
          fontFamily:
            '"SF Mono", "JetBrains Mono", Menlo, Monaco, Consolas, "Liberation Mono", monospace',
          fontSize: 12,
          lineHeight: 1.25,
          cursorBlink: true,
          allowTransparency: true,
          scrollback: 5_000,
          theme: theme(resolvedTheme === "dark"),
        });
        const fit: FitAddonInstance = new fitMod.FitAddon();
        t.loadAddon(fit);
        t.loadAddon(new linksMod.WebLinksAddon());
        t.open(containerRef.current);
        fit.fit();

        term = t;

        // `buildWsUrl` (inside createReconnectingSocket) prefixes the path
        // with env.wsBaseUrl (NEXT_PUBLIC_WS_BASE_URL) and appends ?token=…
        socket = createReconnectingSocket({
          url: `/ws/sessions/${sessionId}/terminal`,
          token: wsToken,
          sessionId,
          // Disable JSON heartbeat pings: the terminal channel is a raw PTY
          // byte stream, so JSON frames would surface as garbage in the
          // user's shell. Reconnect-on-close still keeps the channel alive.
          heartbeatMs: 0,
          onStatusChange: handleStatusChange,
          onOpen() {
            if (socket) flushPending(socket);
          },
          onMessage(ev) {
            const data = ev.data;
            if (typeof data === "string") {
              t.write(data);
            } else if (data instanceof Blob) {
              data
                .arrayBuffer()
                .then((buf) => {
                  t.write(new Uint8Array(buf));
                })
                .catch(() => {
                  // Terminal output is best-effort — swallow a failed Blob
                  // read rather than crashing the workspace.
                });
            } else if (data instanceof ArrayBuffer) {
              t.write(new Uint8Array(data));
            }
          },
          onError() {
            setError("Terminal connection error");
          },
          onAttemptsExhausted() {
            setError("Lost the terminal connection. Refresh to reconnect.");
          },
        });

        // Keystrokes that arrive while the socket is anything other than
        // "open" are buffered FIFO and flushed on the next "open" event.
        t.onData((input: string) => {
          if (socket && socket.status() === "open") {
            socket.send(input);
          } else {
            bufferFrame(input);
          }
        });

        onResize = () => {
          try {
            fit.fit();
            // Binary resize control frame per spec §13 / coordinated with 4.7:
            //   byte 0   : 0x01 (control frame discriminator — "resize")
            //   bytes 1-2: cols (uint16 BE)
            //   bytes 3-4: rows (uint16 BE)
            const frame = encodeResizeFrame(t.cols, t.rows);
            if (socket && socket.status() === "open") {
              socket.send(frame);
            } else {
              bufferFrame(frame);
            }
          } catch {
            /* ignore */
          }
        };
        window.addEventListener("resize", onResize);
        // Initial resize.
        onResize();
      } catch (err) {
        if (!disposed) {
          setError(
            err instanceof Error ? err.message : "Failed to load terminal"
          );
        }
      }
    }

    void boot();

    return () => {
      disposed = true;
      if (onResize) window.removeEventListener("resize", onResize);
      socket?.close();
      try {
        term?.dispose();
      } catch {
        /* ignore */
      }
      // The fit addon is disposed transitively by term.dispose().
    };
    // We deliberately don't re-run on `resolvedTheme` change — theme is
    // synced via a separate effect below so the WS stays alive.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, token]);

  return (
    <div
      className={cn(
        "relative h-full w-full bg-[var(--color-surface-elevated)] p-2",
        className
      )}
      role="region"
      aria-label="Sandbox terminal"
    >
      <div ref={containerRef} className="h-full w-full" />
      <StatusBadge status={status} error={error} />
    </div>
  );
}

function StatusBadge({
  status,
  error,
}: {
  status: ReconnectingSocketStatus;
  error: string | null;
}) {
  if (status === "open" && !error) return null;
  const label = error
    ? "Error"
    : status === "connecting"
      ? "Connecting…"
      : status === "reconnecting"
        ? "Reconnecting…"
        : status === "closed"
          ? "Disconnected"
          : "Idle";

  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none absolute right-2 top-2 inline-flex items-center gap-1 rounded-md bg-[oklch(from_var(--color-background)_l_c_h/0.85)] px-2 py-0.5 text-[10px] font-mono text-[var(--color-muted-foreground)] backdrop-blur"
    >
      {status !== "open" ? <Loader2 className="size-3 animate-spin" aria-hidden /> : null}
      {label}
    </div>
  );
}

/**
 * Encode an `(cols, rows)` PTY resize request as the 5-byte binary control
 * frame the backend expects (see `apps/api/app/ws/terminal.py` parser).
 * Exported so the unit test can pin the wire format.
 */
export function encodeResizeFrame(cols: number, rows: number): Uint8Array {
  const safe = (n: number) =>
    Math.max(0, Math.min(0xffff, Math.floor(Number.isFinite(n) ? n : 0)));
  const c = safe(cols);
  const r = safe(rows);
  return new Uint8Array([
    0x01,
    (c >>> 8) & 0xff,
    c & 0xff,
    (r >>> 8) & 0xff,
    r & 0xff,
  ]);
}

/**
 * xterm.js renders to canvas and needs literal color strings (no CSS vars).
 * Read computed style values from the design tokens so the terminal palette
 * tracks the rest of the UI when the theme is swapped.
 */
function readCssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return v || fallback;
}

function theme(isDark: boolean) {
  const fg = readCssVar("--color-foreground", isDark ? "#e8eaf0" : "#1c1f24");
  const bg = readCssVar("--color-background", isDark ? "#0f1115" : "#ffffff");
  const primary = readCssVar("--color-primary", isDark ? "#7aa2f7" : "#3b6bc7");
  const danger = readCssVar("--color-danger", isDark ? "#ef6f6c" : "#d12f2c");
  const success = readCssVar("--color-success", isDark ? "#7ec699" : "#3f9142");
  const warning = readCssVar("--color-warning", isDark ? "#e6c07b" : "#a07300");
  const muted = readCssVar(
    "--color-muted-foreground",
    isDark ? "#d4d4d4" : "#3b3b3b"
  );
  const selectionBackground = isDark
    ? "rgba(125, 145, 220, 0.35)"
    : "rgba(125, 145, 220, 0.25)";
  return {
    background: bg,
    foreground: fg,
    cursor: fg,
    cursorAccent: bg,
    selectionBackground,
    black: "#000000",
    red: danger,
    green: success,
    yellow: warning,
    blue: primary,
    magenta: isDark ? "#c678dd" : "#8e44ad",
    cyan: isDark ? "#56b6c2" : "#0e7c86",
    white: muted,
  };
}
