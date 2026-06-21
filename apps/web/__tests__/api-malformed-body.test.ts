import {
  describe,
  expect,
  it,
  vi,
  afterEach,
  type MockInstance,
} from "vitest";
import { ApiError, getReport } from "@/lib/api";

/**
 * P2 — success-path JSON parse failures must surface as a typed ApiError at
 * the source, not resolve as ``null as T``.
 *
 * Pre-fix: a 200 ``application/json`` response whose body is truncated /
 * mangled / empty went through ``response.json().catch(() => null)`` and
 * ``request`` returned ``null``. The caller dereferenced it and threw a
 * ``TypeError`` far from the source, and a genuine null was
 * indistinguishable from a corrupt success body.
 *
 * Post-fix:
 *   - a non-empty body that fails to parse throws ``ApiError('malformed
 *     response body', 200)``;
 *   - a 204 still resolves to ``undefined`` (the legitimate no-content path);
 *   - a 200 with a genuinely empty body still resolves cleanly (documented
 *     empty-body endpoints).
 */

type FetchArgs = [input: string | URL | Request, init?: RequestInit];
type FetchMock = MockInstance<(...args: FetchArgs) => Promise<Response>>;

const ORIGINAL_FETCH = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  vi.restoreAllMocks();
});

describe("request() success-path malformed JSON", () => {
  it("throws ApiError (not null) on a 200 application/json body that fails to parse", async () => {
    // A truncated object — valid content-type, non-empty, but not parseable.
    const fetchMock: FetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response('{"score": 4', {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    let caught: unknown = null;
    try {
      await getReport("sub-1");
    } catch (err) {
      caught = err;
    }

    expect(caught).toBeInstanceOf(ApiError);
    const apiErr = caught as ApiError;
    expect(apiErr.status).toBe(200);
    expect(apiErr.message).toBe("malformed response body");
  });

  it("throws ApiError on a 200 with an empty content-type-json but invalid (non-whitespace) garbage body", async () => {
    const fetchMock: FetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response("<html>502 Bad Gateway</html>", {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    await expect(getReport("sub-1")).rejects.toBeInstanceOf(ApiError);
  });

  it("still resolves on a 204 No Content (legitimate empty-body path)", async () => {
    const fetchMock: FetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response(null, {
          status: 204,
          headers: { "content-type": "application/json" },
        }),
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    // getReport types its result, but the runtime resolves to undefined on
    // a 204 short-circuit — assert it does NOT throw.
    await expect(getReport("sub-1")).resolves.toBeUndefined();
  });

  it("still resolves on a 200 with a genuinely empty body (documented empty-body endpoint)", async () => {
    const fetchMock: FetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response("", {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    // An empty (or whitespace-only) body is not a parse error — it resolves
    // to null/undefined so endpoints documented to return no content keep
    // working. The important thing is that it does NOT throw.
    await expect(getReport("sub-1")).resolves.toBeNull();
  });

  it("still parses a well-formed 200 JSON body", async () => {
    const fetchMock: FetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response(JSON.stringify({ id: "sub-1", score: 42 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    const body = (await getReport("sub-1")) as unknown as {
      id: string;
      score: number;
    };
    expect(body).toEqual({ id: "sub-1", score: 42 });
  });
});
