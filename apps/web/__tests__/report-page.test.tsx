import * as React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { Submission } from "@arena/shared-types";

vi.mock("next/link", () => ({
  __esModule: true,
  default: ({
    children,
    href,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
  } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

// useSearchParams is read by ReportView for the optional `?share=` query.
const searchParamsGet = vi.fn<(key: string) => string | null>(() => null);
const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  useSearchParams: () => ({ get: searchParamsGet }),
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
}));

// Stub recharts ResponsiveContainer (jsdom has no layout); render children.
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
const shareReport = vi.fn();
const createSession = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getReport: (...args: unknown[]) => getReport(...args),
    getTimeline: (...args: unknown[]) => getTimeline(...args),
    shareReport: (...args: unknown[]) => shareReport(...args),
    createSession: (...args: unknown[]) => createSession(...args),
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

/**
 * Build a complete Submission fixture. Top-level overrides are merged
 * shallowly so callers can swap individual fields (e.g. `total_score`)
 * per test without re-typing the full skeleton.
 */
function buildSubmission(partial?: Partial<Submission>): Submission {
  const base: Submission = {
    id: "submission-123",
    session_id: "session-abc",
    mission_id: "auth-cookie-expiration",
    final_diff: "diff --git a/file b/file\n",
    visible_test_results: [],
    hidden_test_results: [],
    validator_results: [],
    total_score: 78,
    created_at: "2026-05-21T10:00:00Z",
    ideal_solution:
      "## Reference fix\nAdd an expiration check inside `isValid()`.",
    ideal_solution_diff: null,
    agent_patch_diff: null,
    critical_moments: [],
    verified: false,
    score_report: {
      total: 78,
      missed_failure_mode: false,
      strengths: ["Selected right context", "Asked for regression test"],
      weaknesses: ["Did not run typecheck"],
      badges_earned: ["regression-test-writer"],
      dimensions: {
        final_correctness: { score: 24, max: 30, signals: ["3/4 hidden tests passed"] },
        verification: { score: 14, max: 20, signals: ["ran auth tests"] },
        agent_review: { score: 11, max: 15, signals: ["diff opened"] },
        prompt_quality: { score: 7, max: 10, signals: ["mentions regression"] },
        context_selection: { score: 8, max: 10, signals: ["middleware selected"] },
        safety: { score: 9, max: 10, signals: ["no validation removed"] },
        diff_minimality: { score: 5, max: 5, signals: ["12 lines added"] },
      },
    },
  };
  return { ...base, ...partial };
}

const FIXTURE = buildSubmission();

function renderWithClient(node: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getTimeline.mockResolvedValue([]);
  searchParamsGet.mockReturnValue(null);
  try {
    window.sessionStorage.clear();
  } catch {
    /* jsdom always supplies sessionStorage, but be defensive */
  }
});

describe("ReportView", () => {
  it("renders the radar, dimension breakdown, badges and ideal solution from a mocked getReport", async () => {
    getReport.mockResolvedValue(FIXTURE);

    renderWithClient(<ReportView submissionId="submission-123" />);

    await waitFor(() =>
      // Report header is an <h1> with aria-label="Score 78 out of 100"
      // (the visible content is a giant numeral). Query by role for the
      // accessible name rather than by stale "Your Score:" copy.
      expect(
        screen.getByRole("heading", { name: /Score 78 out of/i }),
      ).toBeInTheDocument()
    );

    expect(getReport).toHaveBeenCalledWith(
      "submission-123",
      null,
      expect.anything()
    );

    // Radar wrapper present.
    expect(screen.getByTestId("responsive-container")).toBeInTheDocument();

    // Dimension labels.
    expect(screen.getByText(/Final patch correctness/i)).toBeInTheDocument();
    expect(screen.getByText(/Safety awareness/i)).toBeInTheDocument();

    // Ideal solution markdown.
    expect(screen.getByText(/Reference fix/i)).toBeInTheDocument();

    // Badges strip.
    expect(screen.getByTestId("badges-strip")).toHaveTextContent(
      "regression-test-writer"
    );
  });

  it("renders the not-found state on 404", async () => {
    const { ApiError } = await import("@/lib/api");
    getReport.mockRejectedValueOnce(new ApiError("Not found", 404, null));

    renderWithClient(<ReportView submissionId="missing" />);

    await waitFor(() =>
      expect(screen.getByText(/Report not found/i)).toBeInTheDocument()
    );
  });

  it("renders the error state (not a crash) when getReport resolves null", async () => {
    getReport.mockResolvedValueOnce(null as unknown as Submission);

    renderWithClient(<ReportView submissionId="submission-123" />);

    await waitFor(() =>
      expect(screen.getByText(/Report not found/i)).toBeInTheDocument()
    );
  });

  it("renders the error state when score_report.dimensions is empty", async () => {
    getReport.mockResolvedValueOnce(
      buildSubmission({
        score_report: {
          total: 0,
          missed_failure_mode: false,
          strengths: [],
          weaknesses: [],
          badges_earned: [],
          // The radar + breakdown components index each key unconditionally;
          // an empty dimensions object would crash render — assert we
          // degrade to the error state instead.
          dimensions: {} as unknown as Submission["score_report"]["dimensions"],
        },
      })
    );

    renderWithClient(<ReportView submissionId="submission-123" />);

    await waitFor(() =>
      expect(screen.getByText(/Report not available/i)).toBeInTheDocument()
    );
    expect(screen.getByText(/incomplete/i)).toBeInTheDocument();
  });

  it("fires report_viewed with complete=true once the report renders fully", async () => {
    getReport.mockResolvedValue(FIXTURE);

    renderWithClient(<ReportView submissionId="submission-123" />);

    await waitFor(() => expect(trackMock).toHaveBeenCalled());
    const call = trackMock.mock.calls.find(
      ([event]) => event === "report_viewed",
    );
    expect(call).toBeDefined();
    expect(call?.[1]).toMatchObject({
      submission_id: "submission-123",
      total_score: 78,
      complete: true,
    });
  });

  it("fires report_viewed with complete=false on a partial/incomplete report", async () => {
    getReport.mockResolvedValue(
      buildSubmission({
        score_report: {
          total: 0,
          missed_failure_mode: false,
          strengths: [],
          weaknesses: [],
          badges_earned: [],
          dimensions: {} as unknown as Submission["score_report"]["dimensions"],
        },
      }),
    );

    renderWithClient(<ReportView submissionId="submission-123" />);

    await waitFor(() =>
      expect(screen.getByText(/Report not available/i)).toBeInTheDocument(),
    );
    const call = trackMock.mock.calls.find(
      ([event]) => event === "report_viewed",
    );
    expect(call).toBeDefined();
    expect(call?.[1]).toMatchObject({
      submission_id: "submission-123",
      complete: false,
    });
  });

  it("persists the retry-after cooldown across unmount/remount via sessionStorage", async () => {
    const { ApiError } = await import("@/lib/api");
    getReport.mockResolvedValue(FIXTURE);
    createSession.mockRejectedValue(
      new ApiError("Too many requests", 429, null, 30),
    );

    const view = renderWithClient(
      <ReportView submissionId="submission-123" />,
    );

    // Wait for the report (and therefore the retry button) to render.
    const retryBtn = await screen.findByTestId("retry-mission-button");
    expect(retryBtn).not.toBeDisabled();

    // First click — triggers the 429 path which persists the deadline.
    await act(async () => {
      fireEvent.click(retryBtn);
    });

    await waitFor(() =>
      expect(screen.getByTestId("retry-mission-button")).toBeDisabled(),
    );
    expect(
      screen.getByTestId("retry-mission-button").textContent ?? "",
    ).toMatch(/try again in/i);

    // The cooldown is now in sessionStorage — the key encodes mission id.
    const stored = window.sessionStorage.getItem(
      "oad.retry_after.auth-cookie-expiration",
    );
    expect(stored).not.toBeNull();
    const persistedDeadline = Number(stored);
    expect(persistedDeadline).toBeGreaterThan(Date.now());

    // Unmount (e.g. the user clicked away to /missions) and remount the
    // report view a moment later.
    view.unmount();
    const remounted = renderWithClient(
      <ReportView submissionId="submission-123" />,
    );
    const reRetry = await remounted.findByTestId("retry-mission-button");
    expect(reRetry).toBeDisabled();
    expect(reRetry.textContent ?? "").toMatch(/try again in/i);
  });

  it("renders the error state when one dimension key is missing", async () => {
    const fixture = buildSubmission();
    const missingOne: Submission = {
      ...fixture,
      score_report: {
        ...(fixture.score_report as NonNullable<Submission["score_report"]>),
        dimensions: {
          // Intentionally omit `diff_minimality` — the radar would crash
          // reading `dim.score` off `undefined`.
          final_correctness: {
            score: 24,
            max: 30,
            signals: [],
          },
          verification: { score: 14, max: 20, signals: [] },
          agent_review: { score: 11, max: 15, signals: [] },
          prompt_quality: { score: 7, max: 10, signals: [] },
          context_selection: { score: 8, max: 10, signals: [] },
          safety: { score: 9, max: 10, signals: [] },
        } as unknown as Submission["score_report"]["dimensions"],
      },
    };
    getReport.mockResolvedValueOnce(missingOne);

    renderWithClient(<ReportView submissionId="submission-123" />);

    await waitFor(() =>
      expect(screen.getByText(/Report not available/i)).toBeInTheDocument()
    );
  });
});
