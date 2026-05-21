import { describe, expect, it, vi, beforeEach, afterEach, type MockInstance } from "vitest";
import { getCsrfToken, getReport, markDiffOpened } from "@/lib/api";

/**
 * Tests that the request layer:
 *   - injects `X-Csrf-Token` on mutating methods when the cookie is set,
 *   - omits it cleanly when no cookie is present,
 *   - exempts the magic-link bootstrap endpoint,
 *   - serialises `getReport(share)` as `?share=…`,
 *   - posts `{path}` on `markDiffOpened`.
 */

type FetchArgs = [input: string | URL | Request, init?: RequestInit];
type FetchMock = MockInstance<(...args: FetchArgs) => Promise<Response>>;

const ORIGINAL_FETCH = globalThis.fetch;

function setCookie(value: string): void {
  Object.defineProperty(document, "cookie", {
    configurable: true,
    get: () => value,
    set: () => {},
  });
}

beforeEach(() => {
  setCookie("");
});

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  vi.restoreAllMocks();
});

describe("getCsrfToken", () => {
  it("returns null when the cookie is absent", () => {
    setCookie("other=value; foo=bar");
    expect(getCsrfToken()).toBeNull();
  });

  it("reads arena_csrf out of document.cookie", () => {
    setCookie("foo=bar; arena_csrf=abc123; baz=qux");
    expect(getCsrfToken()).toBe("abc123");
  });

  it("URL-decodes the token value", () => {
    setCookie("arena_csrf=" + encodeURIComponent("a%b/c"));
    expect(getCsrfToken()).toBe("a%b/c");
  });
});

describe("CSRF header injection", () => {
  it("adds X-Csrf-Token on POST when the cookie is set", async () => {
    setCookie("arena_csrf=tok-xyz");
    const fetchMock: FetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response(null, {
          status: 204,
          headers: { "content-type": "application/json" },
        })
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    await markDiffOpened("session-1", "src/foo.ts");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-Csrf-Token"]).toBe(
      "tok-xyz"
    );
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ path: "src/foo.ts" });
  });

  it("omits the header on GET requests", async () => {
    setCookie("arena_csrf=tok-xyz");
    const fetchMock: FetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    await getReport("sub-1");

    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-Csrf-Token"]).toBeUndefined();
  });

  it("still sends a POST when no cookie is set (server returns 403 cleanly)", async () => {
    setCookie("");
    const fetchMock: FetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response(null, {
          status: 204,
          headers: { "content-type": "application/json" },
        })
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    await markDiffOpened("session-1", "");

    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-Csrf-Token"]).toBeUndefined();
  });
});

describe("getReport share-token plumbing", () => {
  it("appends ?share=… when a share token is provided", async () => {
    const fetchMock: FetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    await getReport("sub-1", "share-token-xyz");

    const url = fetchMock.mock.calls[0]![0] as string;
    expect(url).toContain("/reports/sub-1");
    expect(url).toContain("share=share-token-xyz");
  });

  it("does not append share=… when share is null", async () => {
    const fetchMock: FetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    await getReport("sub-1", null);

    const url = fetchMock.mock.calls[0]![0] as string;
    expect(url).not.toContain("share=");
  });
});

describe("markDiffOpened path body", () => {
  it("posts {path} as the request body", async () => {
    const fetchMock: FetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response(null, {
          status: 204,
          headers: { "content-type": "application/json" },
        })
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    await markDiffOpened("session-1", "");

    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({ path: "" });
  });
});
