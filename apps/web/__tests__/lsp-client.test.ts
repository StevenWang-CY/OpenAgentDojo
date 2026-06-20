/**
 * P1-3 — Unit tests for ``createLSPClient`` and the JSON-RPC framing helpers.
 *
 * We don't open a real WebSocket. ``createLSPClient`` accepts a
 * ``wsFactory`` override; the tests inject a stub that:
 *   - records calls,
 *   - exposes synchronous handles to fire ``onmessage`` / ``onclose``,
 *   - captures every ``send`` so we can assert the JSON-RPC framing bytes.
 *
 * Coverage matrix:
 *   1. Structured ``lsp_error`` text frame BEFORE the initialize handshake
 *      flips state to ``error`` with the expected error class.
 *   2. The byte pump round-trips: feeding a fake ``initialize`` response
 *      back through the socket resolves the in-flight request via the
 *      JSON-RPC id.
 *   3. ``encodeFrame`` / ``FrameDecoder`` agree on the wire format
 *      (encode → decode is identity for a representative message).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createLSPClient,
  LSP_SUBPROTOCOL,
  type LSPState,
} from "@/lib/lsp/client";
import { encodeFrame, FrameDecoder } from "@/lib/lsp/framing";

// ── Fake WebSocket ──────────────────────────────────────────────────────────

interface FakeSocketHandle {
  url: string;
  protocols: string | string[] | undefined;
  sent: Uint8Array[];
  open(): void;
  emitText(data: string): void;
  emitBinary(data: Uint8Array): void;
  emitClose(code?: number, reason?: string): void;
  socket: FakeSocket;
}

class FakeSocket implements Partial<WebSocket> {
  url: string;
  // TS 6 types ``WebSocket.readyState`` as the literal union ``0 | 1 | 2 | 3``,
  // so annotate explicitly rather than letting it widen to ``number``.
  readyState: WebSocket["readyState"] = 0;
  binaryType: BinaryType = "blob";
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  sent: Uint8Array[] = [];
  protocols: string | string[] | undefined;
  constructor(url: string, protocols?: string | string[]) {
    this.url = url;
    this.protocols = protocols;
  }
  send(data: string | ArrayBufferLike | Blob | ArrayBufferView): void {
    if (data instanceof Uint8Array) {
      // Preserve the bytes by copying so later mutation doesn't lie.
      this.sent.push(new Uint8Array(data));
    } else if (data instanceof ArrayBuffer) {
      this.sent.push(new Uint8Array(data.slice(0)));
    } else if (typeof data === "string") {
      this.sent.push(new TextEncoder().encode(data));
    }
  }
  close(_code?: number, _reason?: string): void {
    this.readyState = 3;
    this.onclose?.({ code: _code ?? 1000, reason: _reason ?? "" } as CloseEvent);
  }
}

function makeFakeSocket(): {
  factory: (url: string, protocols?: string | string[]) => WebSocket;
  handles: FakeSocketHandle[];
} {
  const handles: FakeSocketHandle[] = [];
  const factory = (url: string, protocols?: string | string[]): WebSocket => {
    const socket = new FakeSocket(url, protocols);
    const handle: FakeSocketHandle = {
      url,
      protocols,
      sent: socket.sent,
      socket,
      open() {
        socket.readyState = 1;
        socket.onopen?.(new Event("open"));
      },
      emitText(data) {
        socket.onmessage?.({ data } as MessageEvent);
      },
      emitBinary(data) {
        socket.onmessage?.({ data: data.buffer } as MessageEvent);
      },
      emitClose(code = 1006, reason = "") {
        socket.readyState = 3;
        socket.onclose?.({ code, reason } as CloseEvent);
      },
    };
    handles.push(handle);
    return socket as unknown as WebSocket;
  };
  return { factory, handles };
}

// ── Framing round-trip ──────────────────────────────────────────────────────

describe("LSP framing", () => {
  it("encodeFrame + FrameDecoder round-trip a JSON-RPC message", () => {
    const msg = {
      jsonrpc: "2.0" as const,
      id: 1,
      method: "initialize",
      params: { rootUri: "file:///workspace" },
    };
    const bytes = encodeFrame(msg);
    const decoder = new FrameDecoder();
    decoder.push(bytes);
    const decoded = decoder.drain();
    expect(decoded).toHaveLength(1);
    expect(decoded[0]).toEqual(msg);
  });

  it("decodes a chunk that contains two messages back-to-back", () => {
    const a = encodeFrame({ jsonrpc: "2.0", id: 1, method: "ping" });
    const b = encodeFrame({ jsonrpc: "2.0", id: 2, method: "pong" });
    const concat = new Uint8Array(a.byteLength + b.byteLength);
    concat.set(a, 0);
    concat.set(b, a.byteLength);
    const decoder = new FrameDecoder();
    decoder.push(concat);
    const decoded = decoder.drain();
    expect(decoded.map((m) => m.method)).toEqual(["ping", "pong"]);
  });

  it("buffers partial frames until a full body arrives", () => {
    const full = encodeFrame({ jsonrpc: "2.0", id: 1, method: "ping" });
    const decoder = new FrameDecoder();
    decoder.push(full.subarray(0, 10));
    expect(decoder.drain()).toEqual([]);
    decoder.push(full.subarray(10));
    expect(decoder.drain()).toEqual([
      { jsonrpc: "2.0", id: 1, method: "ping" },
    ]);
  });
});

// ── Client behaviour ────────────────────────────────────────────────────────

describe("createLSPClient — structured error frame", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("flips state to error on a pre-initialize lsp_error text frame", () => {
    const { factory, handles } = makeFakeSocket();
    const states: LSPState[] = [];
    createLSPClient({
      sessionId: "00000000-0000-0000-0000-000000000000",
      language: "python",
      wsToken: "tok",
      wsFactory: factory,
      onStateChange: (s) => states.push(s),
    });

    expect(handles).toHaveLength(1);
    const handle = handles[0]!;
    // Backend pattern: send the structured error frame on the heels of
    // accept, then close. The FE shouldn't have sent initialize yet.
    handle.open();
    handle.emitText(
      JSON.stringify({
        type: "lsp_error",
        error: "binary_not_found",
        language: "python",
        detail: null,
      })
    );
    // The backend follows up with a close — emit it so the client
    // tears down. State should already be "error" at that point.
    handle.emitClose(4503, "binary_not_found");

    const last = states[states.length - 1]!;
    expect(last.kind).toBe("error");
    if (last.kind === "error") {
      expect(last.error).toBe("binary_not_found");
      expect(last.language).toBe("python");
    }
  });

  it("encodes initialize with Content-Length framing and resolves on response", async () => {
    const { factory, handles } = makeFakeSocket();
    createLSPClient({
      sessionId: "00000000-0000-0000-0000-000000000000",
      language: "python",
      wsToken: "tok",
      wsFactory: factory,
    });

    const handle = handles[0]!;
    // Subprotocol echoed back from the backend on accept.
    expect(handle.protocols).toEqual([LSP_SUBPROTOCOL]);

    handle.open();
    // Allow microtasks so the initialize request hits ``send``.
    await Promise.resolve();
    await Promise.resolve();
    expect(handle.sent.length).toBeGreaterThan(0);

    // The first ``send`` MUST be a Content-Length-framed initialize.
    const decoder = new FrameDecoder();
    decoder.push(handle.sent[0]!);
    const frames = decoder.drain();
    expect(frames).toHaveLength(1);
    expect(frames[0]).toMatchObject({
      jsonrpc: "2.0",
      method: "initialize",
    });
    expect(frames[0]!.id).toBeDefined();
    const initId = frames[0]!.id;

    // Echo back a faked initialize result on the binary channel and the
    // client should send the ``initialized`` notification.
    const response = encodeFrame({
      jsonrpc: "2.0",
      id: initId as number,
      result: { capabilities: {} },
    });
    handle.emitBinary(response);
    // Microtask drain so the response promise settles + ``initialized``
    // notification is queued.
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    // We expect two sends: [0] = initialize, [1] = initialized.
    expect(handle.sent.length).toBeGreaterThanOrEqual(2);
    const secondDecoder = new FrameDecoder();
    secondDecoder.push(handle.sent[1]!);
    const second = secondDecoder.drain();
    expect(second).toHaveLength(1);
    expect(second[0]!.method).toBe("initialized");
  });

  it("uses the LSP subprotocol on connect", () => {
    const { factory, handles } = makeFakeSocket();
    createLSPClient({
      sessionId: "abc",
      language: "go",
      wsToken: "tok",
      wsFactory: factory,
    });
    expect(handles[0]!.protocols).toEqual([LSP_SUBPROTOCOL]);
    expect(handles[0]!.url).toContain("language=go");
    expect(handles[0]!.url).toContain("token=tok");
    expect(handles[0]!.url).toContain("/ws/sessions/abc/lsp");
  });

  it("emits disconnected on unexpected close after initialize completes", async () => {
    const { factory, handles } = makeFakeSocket();
    const states: LSPState[] = [];
    createLSPClient({
      sessionId: "abc",
      language: "python",
      wsToken: "tok",
      wsFactory: factory,
      onStateChange: (s) => states.push(s),
    });
    const handle = handles[0]!;
    handle.open();
    await Promise.resolve();
    await Promise.resolve();
    // Echo initialize response, then drop the socket.
    const decoder = new FrameDecoder();
    decoder.push(handle.sent[0]!);
    const init = decoder.drain()[0]!;
    handle.emitBinary(
      encodeFrame({ jsonrpc: "2.0", id: init.id as number, result: {} })
    );
    await Promise.resolve();
    await Promise.resolve();
    handle.emitClose(1011, "lsp_crashed");
    const last = states[states.length - 1]!;
    expect(last.kind).toBe("disconnected");
  });
});
