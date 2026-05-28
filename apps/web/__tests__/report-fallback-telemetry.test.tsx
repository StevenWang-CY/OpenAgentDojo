/**
 * P1-2 FE remediation — NextMissionButton fallback fires
 * ``recommendation_shown`` with ``source: "embedded"``.
 *
 * The original ReportView only fired ``recommendation_shown`` when the
 * live ``/me/recommendations`` query returned a shipped mission. The
 * fallback branch — used by anonymous viewers, share-link viewers, and
 * users hitting a transient 401 — relied on the score report's embedded
 * ``feedback_narrative[].recommended_mission_ids`` list, but never
 * surfaced the impression to telemetry. The funnel saw zero recommended
 * mission impressions on share-link views even though the CTA was
 * obviously rendering.
 *
 * This suite locks in:
 *   1. On the embedded path the button fires ``recommendation_shown``
 *      with ``source: "embedded"``, ``signed_in: false``, and
 *      ``weakest_dim: null``.
 *   2. On the live path the button fires ``source: "live"`` (it always
 *      did, but the source tag is new).
 */
import * as React from "react";
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Submission } from "@arena/shared-types";

vi.mock("next/link", () => ({
  __esModule: true,
  default: ({
    children,
    href,
    onClick,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
    onClick?: React.MouseEventHandler<HTMLAnchorElement>;
  } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} onClick={onClick} {...rest}>
      {children}
    </a>
  ),
}));

const searchParamsGet = vi.fn<(key: string) => string | null>(() => null);
vi.mock("next/navigation", () => ({
  useSearchParams: () => ({ get: searchParamsGet }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("recharts", async () => {
  const actual: Record<string, unknown> = await vi.importActual("recharts");
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div data-testid="responsive-container">{children}</div>
    ),
  };
});

const getReport = vi.fn();
const getTimeline = vi.fn();
const getMyRecommendations = vi.fn();
const authMe = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getReport: (...args: unknown[]) => getReport(...args),
    getTimeline: (...args: unknown[]) => getTimeline(...args),
    getMyRecommendations: (...args: unknown[]) => getMyRecommendations(...args),
    auth: {
      ...actual.auth,
      me: (...args: unknown[]) => authMe(...args),
    },
  };
});

const trackMock = vi.fn();
vi.mock("@/lib/telemetry", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/telemetry")>("@/lib/telemetry");
  return {
    ...actual,
    track: (...args: unknown[]) => trackMock(...args),
  };
});

import { ReportView } from "@/components/report/ReportView";

function buildSubmission(partial?: Partial<Submission>): Submission {
  const base: Submission = {
    id: "submission-123",
    session_id: "session-abc",
    mission_id: "auth-cookie-expiration",
    final_diff: "diff --git a/file b/file\n",
    visible_test_results: [],
    hidden_test_results: [],
    validator_results: [],
    total_score: 70,
    created_at: "2026-05-21T10:00:00Z",
    ideal_solution: null,
    ideal_solution_diff: null,
    agent_patch_diff: null,
    critical_moments: [],
    verified: false,
    score_report: {
      total: 70,
      missed_failure_mode: false,
      strengths: [],
      weaknesses: [],
      badges_earned: [],
      feedback_narrative: [
        {
          dimension: "verification",
          score: 5,
          max: 20,
          cause: "skipped verify",
          recommendation: "run tests before submit",
          recommended_mission_ids: ["unverified-claim", "skipped-tests"],
        },
      ],
      dimensions: {
        final_correctness: { score: 20, max: 30, signals: [] },
        verification: { score: 5, max: 20, signals: [] },
        agent_review: { score: 10, max: 15, signals: [] },
        prompt_quality: { score: 7, max: 10, signals: [] },
        context_selection: { score: 8, max: 10, signals: [] },
        safety: { score: 10, max: 10, signals: [] },
        diff_minimality: { score: 5, max: 5, signals: [] },
      },
    },
  };
  return { ...base, ...partial };
}

function renderWithClient(node: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getTimeline.mockResolvedValue([]);
  searchParamsGet.mockReturnValue(null);
});

describe("NextMissionButton — embedded fallback telemetry", () => {
  it("fires recommendation_shown with source=embedded on the share-link path", async () => {
    // Share-link viewer: ``effectiveShare`` is truthy so neither
    // ``auth.me`` nor ``getMyRecommendations`` runs; the button must
    // fall back to ``feedback_narrative[0].recommended_mission_ids[0]``.
    searchParamsGet.mockImplementation((key: string) =>
      key === "share" ? "shr_test" : null,
    );
    getReport.mockResolvedValue(buildSubmission());

    renderWithClient(<ReportView submissionId="submission-123" />);

    await waitFor(() => {
      const shown = trackMock.mock.calls.find(
        ([event]) => event === "recommendation_shown",
      );
      expect(shown).toBeDefined();
    });
    const shown = trackMock.mock.calls.find(
      ([event]) => event === "recommendation_shown",
    );
    expect(shown?.[1]).toMatchObject({
      kind: "report",
      source: "embedded",
      signed_in: false,
      weakest_dim: null,
      mission_ids: ["unverified-claim"],
    });
    // Anonymous-path queries must NOT have fired.
    expect(getMyRecommendations).not.toHaveBeenCalled();
  });

  it("fires recommendation_shown with source=embedded when the live engine returns no shipped items", async () => {
    // Owner viewer with the live engine returning an empty set —
    // simulates a degraded recommendation engine or a brand-new user
    // with no history. The button must still fire impression telemetry
    // tagged ``source: "embedded"``.
    authMe.mockResolvedValue({
      id: "u_1",
      email: "x@example.com",
      handle: "x",
      display_name: "X",
      created_at: "2026-01-01T00:00:00Z",
    });
    getReport.mockResolvedValue(buildSubmission());
    getMyRecommendations.mockResolvedValue({
      weakest_dim: null,
      diagnosis: "",
      recommendations: [],
      computed_at: "2026-05-27T12:00:00Z",
      cache_hit: false,
    });

    renderWithClient(<ReportView submissionId="submission-123" />);

    await waitFor(() => {
      const shown = trackMock.mock.calls.find(
        ([event]) => event === "recommendation_shown",
      );
      expect(shown).toBeDefined();
    });
    const shown = trackMock.mock.calls.find(
      ([event]) => event === "recommendation_shown",
    );
    expect(shown?.[1]).toMatchObject({
      kind: "report",
      source: "embedded",
      signed_in: false,
      weakest_dim: null,
      mission_ids: ["unverified-claim"],
    });
  });
});
