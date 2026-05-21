import * as React from "react";
import { render, screen, waitFor } from "@testing-library/react";
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
vi.mock("next/navigation", () => ({
  useSearchParams: () => ({ get: searchParamsGet }),
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

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getReport: (...args: unknown[]) => getReport(...args),
    getTimeline: (...args: unknown[]) => getTimeline(...args),
    shareReport: (...args: unknown[]) => shareReport(...args),
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
    final_diff: "diff --git a/file b/file\n",
    visible_test_results: [],
    hidden_test_results: [],
    validator_results: [],
    total_score: 78,
    created_at: "2026-05-21T10:00:00Z",
    ideal_solution:
      "## Reference fix\nAdd an expiration check inside `isValid()`.",
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
});

describe("ReportView", () => {
  it("renders the radar, dimension breakdown, badges and ideal solution from a mocked getReport", async () => {
    getReport.mockResolvedValue(FIXTURE);

    renderWithClient(<ReportView submissionId="submission-123" />);

    await waitFor(() =>
      expect(screen.getByText(/Your Score:/i)).toBeInTheDocument()
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
});
