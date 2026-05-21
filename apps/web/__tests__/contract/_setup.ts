/**
 * Shared MSW + fetch shim for contract tests.
 *
 * Vitest runs in jsdom, which gives us `fetch`, but `lib/api.ts` builds a
 * URL against `env.apiBaseUrl` (defaults to `http://localhost:8000`).
 * Each suite registers its own MSW handlers and then runs the real
 * `api.*` function — proving the request shape AND the response parsing
 * match what `apps/api/openapi.json` describes.
 */
import { afterAll, afterEach, beforeAll } from "vitest";
import { setupServer } from "msw/node";
import type { RequestHandler } from "msw";

export const server = setupServer();

export function withContractServer(handlers: RequestHandler[]): void {
  beforeAll(() => {
    server.listen({ onUnhandledRequest: "error" });
    server.use(...handlers);
  });
  afterEach(() => {
    server.resetHandlers(...handlers);
  });
  afterAll(() => server.close());
}

/**
 * Run a structural shape check against an object. We don't pull in zod just
 * for these tests — the value already passed TypeScript at compile-time, so
 * this catches the runtime case where the response is silently the wrong
 * type (e.g. backend dropped a required field).
 */
export function expectShape<T extends Record<string, unknown>>(
  value: T,
  required: ReadonlyArray<keyof T>
): void {
  for (const key of required) {
    if (!(key in value)) {
      throw new Error(`missing required key: ${String(key)}`);
    }
  }
}

export const API_BASE = "http://localhost:8000";
