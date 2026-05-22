import {
  describe,
  expect,
  it,
  vi,
  afterEach,
  type MockInstance,
} from "vitest";
import { ApiError, listMissions } from "@/lib/api";
import { env } from "@/lib/env";

/**
 * Verifies that when `fetch` rejects (network / CORS / DNS failure), the
 * thrown ApiError carries the FULL URL — origin + path + query — so the
 * developer can immediately see which backend host failed instead of being
 * shown only the path (which is identical across envs and useless for
 * triage).
 */

type FetchArgs = [input: string | URL | Request, init?: RequestInit];
type FetchMock = MockInstance<(...args: FetchArgs) => Promise<Response>>;

const ORIGINAL_FETCH = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  vi.restoreAllMocks();
});

describe("ApiError network failure message", () => {
  it("includes the full URL (origin + path) when fetch rejects", async () => {
    const fetchMock: FetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) => {
        throw new TypeError("Failed to fetch");
      }
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    let caught: unknown = null;
    try {
      await listMissions();
    } catch (err) {
      caught = err;
    }

    expect(caught).toBeInstanceOf(ApiError);
    const apiErr = caught as ApiError;
    expect(apiErr.status).toBe(0);
    // The path alone (which is what the pre-fix message used) would just be
    // "/missions" — assert the full origin is present too.
    expect(apiErr.message).toContain(env.apiBaseUrl);
    expect(apiErr.message).toContain("/api/v1/missions");
  });
});
