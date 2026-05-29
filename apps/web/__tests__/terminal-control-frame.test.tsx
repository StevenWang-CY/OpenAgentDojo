/**
 * p2-terminal-ws-json-error-frame-unparsed — the terminal WS onMessage
 * handler must intercept the backend's JSON error control frame instead of
 * writing it to xterm as raw bytes.
 *
 * The backend (apps/api/app/ws/terminal.py) sends
 *   {"type":"error","code":"no_sandbox","detail":"session has no sandbox"}
 * then closes with 1011. Writing that to xterm dumped JSON into the user's
 * shell, and the 1011 close (not fatal) triggered reconnect-backoff that
 * re-printed it on every attempt. The fix parses well-formed control frames
 * and surfaces ``detail`` through the component's error badge; plain PTY
 * output (including a rare non-JSON line starting with ``{``) is still
 * written byte-faithfully.
 */
import * as React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import {
  errorMessageForControlFrame,
} from "@/components/workspace/Terminal";

// ── Pure-policy tests for the control-frame parser ──────────────────────────

describe("errorMessageForControlFrame", () => {
  it("surfaces ``detail`` from a well-formed error control frame", () => {
    expect(
      errorMessageForControlFrame(
        JSON.stringify({
          type: "error",
          code: "no_sandbox",
          detail: "session has no sandbox",
        }),
      ),
    ).toBe("session has no sandbox");
  });

  it("falls back to the code when ``detail`` is absent", () => {
    expect(
      errorMessageForControlFrame(JSON.stringify({ type: "error", code: "attach_failed" })),
    ).toBe("Terminal error: attach_failed");
  });

  it("returns null for plain PTY output (not a JSON object)", () => {
    expect(errorMessageForControlFrame("$ ls -la\r\n")).toBeNull();
    expect(errorMessageForControlFrame("")).toBeNull();
  });

  it("returns null for a JSON object frame without an error type", () => {
    expect(
      errorMessageForControlFrame(JSON.stringify({ type: "pong" })),
    ).toBeNull();
  });

  it("returns null for a non-JSON line that merely starts with '{'", () => {
    // Shell brace expansion echoed back — must be treated as terminal bytes.
    expect(errorMessageForControlFrame("{1..3} expands")).toBeNull();
  });
});

// ── Component wiring: error frame → badge, never written to xterm ───────────

// Capture the onMessage callback the Terminal hands to the socket factory so
// the test can drive frames through it directly.
type WsOpts = {
  onMessage?: (ev: MessageEvent) => void;
  onClose?: (ev: CloseEvent) => void;
};
let capturedOpts: WsOpts | null = null;
const socketClose = vi.fn();
vi.mock("@/lib/ws", () => ({
  createReconnectingSocket: (opts: WsOpts) => {
    capturedOpts = opts;
    return {
      send: vi.fn(),
      close: socketClose,
      status: () => "open" as const,
    };
  },
  buildWsUrl: (s: string) => s,
}));

// Mint a token without a network round-trip.
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getWsToken: vi.fn(async () => ({ token: "ws-test", ttl_seconds: 60 })),
  };
});

// Spy on xterm's write so we can assert what does (and doesn't) reach the PTY
// surface. The fit/web-links addons + CSS import are stubbed to no-ops.
const xtermWrite = vi.fn();
vi.mock("xterm", () => ({
  Terminal: class {
    cols = 80;
    rows = 24;
    write = xtermWrite;
    loadAddon() {}
    open() {}
    onData() {}
    dispose() {}
  },
}));
vi.mock("xterm-addon-fit", () => ({
  FitAddon: class {
    fit() {}
  },
}));
vi.mock("xterm-addon-web-links", () => ({
  WebLinksAddon: class {},
}));
vi.mock("xterm/css/xterm.css", () => ({}));

import { Terminal } from "@/components/workspace/Terminal";

beforeEach(() => {
  capturedOpts = null;
  xtermWrite.mockClear();
  socketClose.mockClear();
});

afterEach(() => {
  vi.clearAllMocks();
});

async function mountTerminal() {
  render(<Terminal sessionId="session-1" token="boot-token" />);
  // boot() runs async (dynamic imports + token promise); wait for the socket
  // factory to receive the options carrying onMessage.
  await waitFor(() => expect(capturedOpts).not.toBeNull());
}

function frame(data: string): MessageEvent {
  return { data } as MessageEvent;
}

describe("Terminal onMessage — JSON error control frame", () => {
  it("surfaces the error frame in the badge and does NOT write it to xterm", async () => {
    await mountTerminal();

    act(() => {
      capturedOpts!.onMessage!(
        frame(
          JSON.stringify({
            type: "error",
            code: "no_sandbox",
            detail: "session has no sandbox",
          }),
        ),
      );
    });

    // The parsed detail is surfaced via the error badge…
    await waitFor(() =>
      expect(screen.getByText("session has no sandbox")).toBeInTheDocument(),
    );
    // …and the raw JSON never reached the terminal surface.
    expect(xtermWrite).not.toHaveBeenCalled();
  });

  it("writes normal PTY output byte-faithfully to xterm", async () => {
    await mountTerminal();

    act(() => {
      capturedOpts!.onMessage!(frame("user@box:~$ ls\r\n"));
    });

    expect(xtermWrite).toHaveBeenCalledWith("user@box:~$ ls\r\n");
    // A normal frame must not flip the error badge.
    expect(
      screen.queryByText(/session has no sandbox/i),
    ).not.toBeInTheDocument();
  });

  it("writes a non-JSON line starting with '{' to xterm (no false positive)", async () => {
    await mountTerminal();

    act(() => {
      capturedOpts!.onMessage!(frame("{not json after all"));
    });

    expect(xtermWrite).toHaveBeenCalledWith("{not json after all");
  });
});
