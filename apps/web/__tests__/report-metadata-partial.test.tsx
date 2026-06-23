/**
 * P2 (defensive) — SSR report metadata must degrade gracefully on a partial /
 * legacy ``score_report``.
 *
 * ``generateMetadata`` read ``submission.score_report.strengths.map`` with
 * only a top-level ``if (!submission)`` guard. A submission whose
 * ``score_report`` is null (or whose ``strengths`` is absent) threw during
 * metadata generation, 500ing the crawler/unfurl request. The fix
 * null-coalesces so the generic summary is used instead.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ReportView is a client component pulled in at page-module scope; stub it so
// importing the page in jsdom doesn't drag the whole report tree in.
vi.mock("@/components/report/ReportView", () => ({
  ReportView: () => null,
}));

import { generateMetadata } from "@/app/(app)/report/[submissionId]/page";

const SUBMISSION_ID = "11111111-1111-1111-1111-111111111111";

type FetchInit = RequestInit | undefined;
const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", (url: string, init: FetchInit) => fetchMock(url, init));
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

function callMetadata() {
  return generateMetadata({
    params: Promise.resolve({ submissionId: SUBMISSION_ID }),
    searchParams: Promise.resolve({}),
  });
}

describe("report generateMetadata — partial score_report", () => {
  it("falls back to the generic summary when score_report is null (no throw)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        id: SUBMISSION_ID,
        total_score: 70,
        score_report: null,
      }),
    );

    const meta = await callMetadata();
    expect(meta.title).toBe("Score 70/100 · OpenAgentDojo");
    expect(meta.description).toMatch(/Process-driven supervision grading/i);
  });

  it("falls back to the generic summary when strengths is absent (no throw)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        id: SUBMISSION_ID,
        total_score: 88,
        // score_report present but missing ``strengths`` — the legacy/partial
        // shape that previously threw on ``.strengths.map``.
        score_report: { total: 88, dimensions: {} },
      }),
    );

    const meta = await callMetadata();
    expect(meta.title).toBe("Score 88/100 · OpenAgentDojo");
    expect(meta.description).toMatch(/Process-driven supervision grading/i);
  });

  it("still summarises real strengths when present", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        id: SUBMISSION_ID,
        total_score: 92,
        score_report: {
          total: 92,
          strengths: ["caught the regression", "tight diff"],
        },
      }),
    );

    const meta = await callMetadata();
    expect(meta.description).toBe("caught the regression · tight diff");
  });
});
