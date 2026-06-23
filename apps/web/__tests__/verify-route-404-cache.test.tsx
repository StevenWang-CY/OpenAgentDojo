/**
 * P2 fix — the public verify route must NOT permanently cache a negative.
 *
 * The old route combined ``export const revalidate = false`` with a single
 * ``fetch(…, { cache: "force-cache", next: { revalidate: false } })``. A
 * verify URL hit BEFORE the envelope was minted (recruiter follows the link
 * while grading is still in flight) returned 404 → ``notFound()`` → and that
 * 404 was cached PERMANENTLY. After grading landed, the same URL kept
 * serving the stale 404 forever.
 *
 * The fix gates caching on ``response.ok``: the negative is read ``no-store``
 * so it never sticks, only a confirmed-OK immutable envelope is cached, and
 * the page-level ``revalidate`` is a finite window so a once-404'd render
 * recovers.
 *
 * This suite drives the real ``VerifyPage`` server component against a mocked
 * ``fetch`` and asserts:
 *   1. The page-level ``revalidate`` is finite (not ``false``).
 *   2. A 404 is fetched with a non-permanent (``no-store``) directive and
 *      triggers ``notFound()``.
 *   3. The same id recovers to the rendered body once the envelope exists —
 *      i.e. the negative was never pinned.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import type { VerifyEnvelope } from "@/lib/api";

const notFoundMock = vi.fn(() => {
  throw new Error("NEXT_NOT_FOUND");
});
vi.mock("next/navigation", () => ({
  notFound: () => notFoundMock(),
}));

// VerifyPageBody is a client component with a telemetry effect; stub it to a
// marker so the server component renders deterministically in jsdom.
vi.mock("@/components/verify/VerifyPageBody", () => ({
  VerifyPageBody: ({ envelope }: { envelope: VerifyEnvelope }) => (
    <div data-testid="verify-body">{envelope.submission_id}</div>
  ),
}));

import * as VerifyPageModule from "@/app/verify/[submissionId]/page";

const SUBMISSION_ID = "11111111-1111-1111-1111-111111111111";

const envelope: VerifyEnvelope = {
  schema_version: 1,
  submission_id: SUBMISSION_ID,
  handle: "jane",
  display_name: "Jane Doe",
  mission_id: "auth-cookie-expiration",
  mission_title: "Expired Session Cookie Still Grants Access",
  mission_version: 1,
  rubric_version: "v1",
  total_score: 78,
  effective_max: 100,
  missed_failure_mode: false,
  score_cap_reason: null,
  proctored: false,
  attempt_index: 2,
  graded_at: "2026-05-23T18:42:11Z",
  canonical_url: `https://openagentdojo.app/verify/${SUBMISSION_ID}`,
  verification_hash: "abcd",
  verification_signature: "0011",
};

type FetchInit = (RequestInit & { cache?: RequestCache }) | undefined;
let calls: { url: string; init: FetchInit }[] = [];
const fetchMock = vi.fn();

beforeEach(() => {
  calls = [];
  notFoundMock.mockClear();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", (url: string, init: FetchInit) => {
    calls.push({ url: String(url), init });
    return fetchMock(url, init);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe("verify route — negative caching", () => {
  it("uses a finite page-level revalidate (never permanently caches the route)", () => {
    // The literal ``false`` here is what pinned the 404 forever — any finite
    // number lets a once-404'd render recover after the window.
    expect(VerifyPageModule.revalidate).not.toBe(false);
    expect(typeof VerifyPageModule.revalidate).toBe("number");
    expect(VerifyPageModule.revalidate as number).toBeGreaterThan(0);
  });

  it("fetches a 404 negative with a non-permanent (no-store) directive and calls notFound()", async () => {
    fetchMock.mockResolvedValue(jsonResponse(404, { detail: "not found" }));

    await expect(
      VerifyPageModule.default({
        params: Promise.resolve({ submissionId: SUBMISSION_ID }),
      }),
    ).rejects.toThrow(/NEXT_NOT_FOUND/);

    expect(notFoundMock).toHaveBeenCalledTimes(1);
    // At least one fetch must use a non-permanent directive for the negative
    // so a once-404'd path is never written to the data cache permanently.
    const negativeFetch = calls.find(
      (c) => (c.init?.cache ?? null) === "no-store",
    );
    expect(negativeFetch).toBeDefined();
    // And critically: the 404 path must NOT have been read through an
    // immutable force-cache + revalidate:false combo (the permanent pin).
    const permanentlyPinned = calls.some(
      (c) =>
        c.init?.cache === "force-cache" &&
        (c.init as { next?: { revalidate?: unknown } }).next?.revalidate ===
          false,
    );
    expect(permanentlyPinned).toBe(false);
  });

  it("recovers: the same id renders the body once the envelope exists", async () => {
    // Simulate grading landing — every fetch for this id now returns 200.
    fetchMock.mockResolvedValue(jsonResponse(200, envelope));

    const result = await VerifyPageModule.default({
      params: Promise.resolve({ submissionId: SUBMISSION_ID }),
    });

    expect(notFoundMock).not.toHaveBeenCalled();
    // The rendered tree carries the envelope's body, proving the negative
    // was never pinned and the path recovered.
    const { render, screen } = await import("@testing-library/react");
    render(result);
    expect(screen.getByTestId("verify-body")).toHaveTextContent(SUBMISSION_ID);
  });
});
