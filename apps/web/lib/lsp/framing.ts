/**
 * LSP JSON-RPC framing helpers.
 *
 * The Language Server Protocol wraps every JSON-RPC message in a
 * ``Content-Length: <n>\r\n\r\n<body>`` header. The backend WS proxy pumps
 * the raw bytes verbatim, so the framing happens on this side — we encode
 * outgoing messages and decode incoming chunks into discrete JSON-RPC
 * messages.
 *
 * The decoder is a small streaming state machine: bytes arrive in arbitrary
 * chunk sizes (each WS binary frame is one stdout chunk from the language
 * server), so we buffer until we have a full Content-Length-bounded body.
 */

const HEADER_SEPARATOR = "\r\n\r\n";
const CONTENT_LENGTH_RE = /Content-Length:\s*(\d+)/i;

/**
 * JSON-RPC 2.0 message shape we care about. We don't validate the wire
 * spec strictly — the language server is trusted on this channel and any
 * malformed frame would just surface as a Monaco-side render quirk, not a
 * security problem.
 */
export interface JsonRpcMessage {
  jsonrpc: "2.0";
  id?: number | string | null;
  method?: string;
  params?: unknown;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

/**
 * Encode a JSON-RPC object into a framed UTF-8 byte buffer.
 *
 * The result is explicitly backed by a concrete ``ArrayBuffer`` (it always
 * allocates a fresh ``new Uint8Array(n)``) so it satisfies ``WebSocket.send``,
 * whose TS 6 ``BufferSource`` signature rejects the ``SharedArrayBuffer`` arm
 * of the default ``Uint8Array<ArrayBufferLike>``.
 */
export function encodeFrame(message: JsonRpcMessage): Uint8Array<ArrayBuffer> {
  const body = JSON.stringify(message);
  // ``TextEncoder`` is used so multibyte chars (rare in JSON-RPC, but cheap
  // to be correct about) compute the right Content-Length.
  const enc = new TextEncoder();
  const bodyBytes = enc.encode(body);
  const header = enc.encode(`Content-Length: ${bodyBytes.byteLength}\r\n\r\n`);
  const out = new Uint8Array(header.byteLength + bodyBytes.byteLength);
  out.set(header, 0);
  out.set(bodyBytes, header.byteLength);
  return out;
}

/**
 * Streaming decoder. Feed chunks of bytes via ``push``; pull whole
 * JSON-RPC messages via ``drain``. The internal buffer grows until at
 * least one full message is available, then the consumed bytes are
 * trimmed off in one shot to keep the buffer compact.
 */
export class FrameDecoder {
  private buf: Uint8Array = new Uint8Array(0);
  private readonly decoder = new TextDecoder("utf-8");

  push(chunk: Uint8Array): void {
    if (chunk.byteLength === 0) return;
    if (this.buf.byteLength === 0) {
      this.buf = chunk;
      return;
    }
    const next = new Uint8Array(this.buf.byteLength + chunk.byteLength);
    next.set(this.buf, 0);
    next.set(chunk, this.buf.byteLength);
    this.buf = next;
  }

  /**
   * Decode and return every complete message currently in the buffer.
   * Partial messages stay buffered for the next ``push``.
   */
  drain(): JsonRpcMessage[] {
    const out: JsonRpcMessage[] = [];
    while (this.buf.byteLength > 0) {
      // Find the header/body separator. We do this by ASCII scan rather
      // than full-buffer decode so a huge body doesn't cost a UTF-8
      // round-trip on every iteration.
      const sepIdx = findSeparator(this.buf);
      if (sepIdx < 0) return out; // header not complete yet
      const headerText = this.decoder.decode(this.buf.subarray(0, sepIdx));
      const match = CONTENT_LENGTH_RE.exec(headerText);
      if (!match) {
        // Malformed header — drop one byte and resync. This is the
        // worst-case "the server sent garbage" path; the alternative
        // (drop everything) would stall the channel.
        this.buf = this.buf.subarray(1);
        continue;
      }
      const len = Number.parseInt(match[1]!, 10);
      const bodyStart = sepIdx + HEADER_SEPARATOR.length;
      if (this.buf.byteLength < bodyStart + len) {
        return out; // body not complete yet
      }
      const bodyBytes = this.buf.subarray(bodyStart, bodyStart + len);
      const bodyText = this.decoder.decode(bodyBytes);
      try {
        const parsed = JSON.parse(bodyText) as JsonRpcMessage;
        out.push(parsed);
      } catch {
        // Parse failure — same recovery as above; we resync past the
        // bad body so subsequent frames still land.
      }
      this.buf = this.buf.subarray(bodyStart + len);
    }
    return out;
  }
}

function findSeparator(buf: Uint8Array): number {
  // Search for the 4-byte sequence \r\n\r\n. Bounded by buffer length so
  // we never run off the end. Most LSP headers are <100 bytes, so the
  // linear scan is fine.
  const cr = 0x0d;
  const lf = 0x0a;
  for (let i = 0; i + 3 < buf.byteLength; i++) {
    if (
      buf[i] === cr &&
      buf[i + 1] === lf &&
      buf[i + 2] === cr &&
      buf[i + 3] === lf
    ) {
      return i;
    }
  }
  return -1;
}
