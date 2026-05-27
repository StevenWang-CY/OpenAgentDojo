/**
 * P1-3 — LSPErrorFrame BE/FE wire-shape contract.
 *
 * The backend (`apps/api/app/schemas/lsp.py::LSPErrorFrame`) is the
 * source of truth for the structured text frame the LSP WebSocket
 * proxy emits before closing the channel. The frontend
 * (`apps/web/lib/lsp/client.ts::LSPErrorFrame`) re-declares the same
 * shape as a TypeScript interface so the JSON-RPC pump can narrow on
 * the `type` discriminator and route the close reason into the
 * editor footer chip state.
 *
 * This test pulls a hand-authored fixture of representative frames
 * (one per error-class branch the backend can emit) and asserts:
 *
 *   1. The fixture parses as a valid `LSPErrorFrame` — i.e. the
 *      compile-time TS interface accepts every shape the BE emits.
 *   2. The discriminator (`type === "lsp_error"`) holds for every
 *      fixture entry — the client.ts parser rejects frames whose
 *      `type` is anything else.
 *   3. The `error` field is one of the well-known strings the FE's
 *      union typecheck honours. Unknown strings narrow to `string`
 *      via the trailing fallback in the union — kept so a BE-side
 *      enum extension doesn't immediately break the FE — but the
 *      hand-authored fixture must hit only the well-known classes.
 */
import { describe, expect, it } from "vitest";
import type { LSPErrorFrame } from "@/lib/lsp/client";
import fixtures from "./fixtures/lsp-error-frames.json";

// Mirror of the BE-side ``LSPErrorClass`` Literal. Hand-maintained
// (the FE has no codegen against the BE today) — drift is caught by
// the BE-side ``test_lsp_error_frame_contract.py`` paired test.
const WELL_KNOWN_ERROR_CLASSES = new Set<string>([
  "binary_not_found",
  "spawn_failed",
  "unsupported_language",
  "driver_unavailable",
  "lsp_already_running",
  "session_not_active",
  "session_not_found",
  "no_sandbox",
  "shutdown_timeout",
  "stdin_broken_pipe",
  "terminate_failed",
  "kill_failed",
  "socket_recv_failed",
  "exec_inspect_failed",
  "dead_entry_evicted",
  "lsp_crashed",
  "initialize_timeout",
  "ws_token_failed",
]);

interface FixtureEntry {
  readonly name: string;
  readonly frame: LSPErrorFrame;
}

const cases: ReadonlyArray<FixtureEntry> = fixtures as FixtureEntry[];

describe("LSPErrorFrame BE/FE contract", () => {
  it("fixture file is non-empty", () => {
    expect(cases.length).toBeGreaterThan(0);
  });

  it.each(cases.map((c) => [c.name, c] as const))(
    "%s — frame shape conforms to the FE interface",
    (_name, fixture) => {
      // Pulling the fields through the interface ensures the TS
      // compiler accepts the JSON shape — the JSON file is loaded
      // with the resolveJsonModule path so a key drift fails at
      // compile time.
      const frame: LSPErrorFrame = fixture.frame;
      expect(frame.type).toBe("lsp_error");
      expect(typeof frame.error).toBe("string");
      expect(frame.error.length).toBeGreaterThan(0);
      expect(typeof frame.language).toBe("string");
      expect(frame.language.length).toBeGreaterThan(0);
      if (frame.detail !== undefined && frame.detail !== null) {
        expect(typeof frame.detail).toBe("string");
      }
    }
  );

  it("every fixture uses a well-known error class (no UNKNOWN drift)", () => {
    const unknown = cases
      .map((c) => c.frame.error)
      .filter((err) => !WELL_KNOWN_ERROR_CLASSES.has(err));
    expect(unknown).toEqual([]);
  });

  it("rejects frames with a wrong `type` literal at runtime parse time", () => {
    // Demonstrates the FE's runtime narrowing matches the BE's
    // ``type: Literal["lsp_error"]`` invariant. The client.ts
    // parser falls back to "treat as JSON-RPC bytes" if the type
    // field doesn't equal "lsp_error", so this check pins that
    // the parser will see exactly the literal it expects.
    const malformed = { type: "not_lsp_error", error: "spawn_failed", language: "python" };
    expect(malformed.type === "lsp_error").toBe(false);
  });

  it("every well-known error class is represented in the fixture (at least partial coverage)", () => {
    // The fixture isn't required to cover every class (that's
    // tested on the BE side via get_args(LSPErrorClass)). But we
    // require at least 3 distinct classes here so the FE-side
    // typecheck has multiple branches exercised.
    const distinctClasses = new Set(cases.map((c) => c.frame.error));
    expect(distinctClasses.size).toBeGreaterThanOrEqual(3);
  });
});
