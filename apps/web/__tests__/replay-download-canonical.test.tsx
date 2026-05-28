/**
 * P1-6 FE remediation — replay JSON download preserves canonical bytes.
 *
 * The original ShareDropdown handler did
 * ``JSON.stringify(JSON.parse(serverBody))`` which round-tripped the
 * artefact through a JS object and silently lost the canonical key
 * ordering and whitespace the backend builder establishes. The
 * downloaded file was no longer byte-identical to what the backend
 * emitted, which broke replay-hash verification for anyone who
 * compared their downloaded JSON to the verify-secret-signed bytes.
 *
 * This suite locks in:
 *   1. ``downloadReplayJson`` sends the response blob to disk verbatim
 *      (the saved blob's bytes equal the server-emitted bytes byte for
 *      byte — no parse / re-stringify round-trip).
 *   2. The return shape is ``{bytes, filename}`` and ``bytes`` is the
 *      blob size — NOT a recomputed ``JSON.stringify`` length.
 *   3. The Content-Disposition filename probe still works end-to-end.
 */
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import { downloadReplayJson } from "@/lib/api";

const SUBMISSION_ID = "11111111-2222-3333-4444-555555555555";

// Deterministic non-canonical JSON the backend serves. Note the
// extra whitespace and the key order — both would be lost on a
// ``JSON.parse → JSON.stringify`` round-trip, but must survive the
// downloadReplayJson path verbatim.
const CANONICAL_BYTES =
  '{"schema_version":1,"submission_id":"abc","events":[{"id":2,"kind":"prompt"},{"id":1,"kind":"command"}]}\n';

let capturedBlob: Blob | null = null;
let createdAnchors: HTMLAnchorElement[] = [];

const originalAnchorClick = HTMLAnchorElement.prototype.click;
const originalFetch = globalThis.fetch;
const originalCreateObjectURL = (URL as unknown as { createObjectURL?: unknown })
  .createObjectURL;
const originalRevokeObjectURL = (URL as unknown as { revokeObjectURL?: unknown })
  .revokeObjectURL;

beforeEach(() => {
  capturedBlob = null;
  createdAnchors = [];

  HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
    createdAnchors.push(this);
  };

  // Capture the blob the helper hands off to URL.createObjectURL so we
  // can compare its bytes against the server-emitted bytes directly. We
  // hold a reference to the blob and read it asynchronously AFTER the
  // download returns (the blob outlives the createObjectURL stub).
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (URL as any).createObjectURL = (blob: Blob) => {
    capturedBlob = blob;
    return "blob:canonical";
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (URL as any).revokeObjectURL = vi.fn();

  // Mock fetch to return exactly the canonical bytes (no JSON parse).
  // Use a fresh Uint8Array body per call — passing a Blob through a
  // Response in jsdom can produce a stream that truncates partway when
  // re-read via ``.blob()`` on the consumer side.
  globalThis.fetch = vi.fn(async () => {
    const bodyBytes = new TextEncoder().encode(CANONICAL_BYTES);
    return new Response(bodyBytes, {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        "Content-Disposition":
          'attachment; filename="arena-replay-11111111-20260528.json"',
      },
    });
  }) as unknown as typeof fetch;
});

afterEach(() => {
  HTMLAnchorElement.prototype.click = originalAnchorClick;
  globalThis.fetch = originalFetch;
  // Restore (or leave as a no-op) so the deferred ``setTimeout(0)``
  // cleanup inside lib/api.ts doesn't blow up after the test finishes.
  // jsdom doesn't ship these helpers natively, so the "original" is
  // ``undefined`` — we keep a no-op stub installed during teardown.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (URL as any).createObjectURL =
    originalCreateObjectURL ?? (() => "blob:noop");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (URL as any).revokeObjectURL = originalRevokeObjectURL ?? (() => undefined);
});

describe("downloadReplayJson — canonical bytes preserved", () => {
  it("hands the server bytes to the anchor without JSON round-tripping", async () => {
    const result = await downloadReplayJson(SUBMISSION_ID);

    expect(capturedBlob).not.toBeNull();
    // Read the captured blob's bytes; this is the exact payload that
    // ends up on disk after the anchor click.
    const buf = await (capturedBlob as Blob).arrayBuffer();
    const capturedBytes = new Uint8Array(buf);
    const expected = new TextEncoder().encode(CANONICAL_BYTES);
    expect(Array.from(capturedBytes)).toEqual(Array.from(expected));

    // The reported byte count must equal the on-the-wire size — NOT a
    // re-serialised JSON.stringify length (which would differ because of
    // the canonical whitespace + key order in the source bytes).
    expect(result.bytes).toBe(expected.byteLength);
    // Sanity: confirm a round-trip would diverge so the test is
    // exercising the real risk, not just a coincidentally-equal length.
    const roundTripped = JSON.stringify(JSON.parse(CANONICAL_BYTES));
    expect(roundTripped.length).not.toBe(CANONICAL_BYTES.length);
  });

  it("uses the Content-Disposition filename when present", async () => {
    const result = await downloadReplayJson(SUBMISSION_ID);
    expect(result.filename).toBe("arena-replay-11111111-20260528.json");
    // The anchor must carry the same download attribute so the saved
    // file is named consistently across browsers.
    expect(createdAnchors.length).toBeGreaterThan(0);
    const anchor = createdAnchors[createdAnchors.length - 1];
    expect(anchor?.download).toBe("arena-replay-11111111-20260528.json");
  });

  it("falls back to the short-id filename when no Content-Disposition is sent", async () => {
    globalThis.fetch = vi.fn(async () => {
      const bodyBytes = new TextEncoder().encode(CANONICAL_BYTES);
      const blob = new Blob([bodyBytes], { type: "application/json" });
      return new Response(blob, {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;

    const result = await downloadReplayJson(SUBMISSION_ID);
    expect(result.filename).toBe(`arena-replay-${SUBMISSION_ID.slice(0, 8)}.json`);
  });
});
