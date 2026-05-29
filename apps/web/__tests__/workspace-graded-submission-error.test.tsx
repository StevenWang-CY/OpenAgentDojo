/**
 * p2-graded-submission-fetch-stuck — when a session is ``graded`` but
 * GET /sessions/{id}/submission errors, the user was stranded on an
 * infinite "Taking you to your report" spinner: the redirect effect only
 * fires on ``submissionQuery.data`` and the spinner branch had no
 * ``isError`` handling (``retry: false`` means a single blip is terminal).
 *
 * The fix renders a recoverable error UI (Retry + Back to missions) in the
 * graded branch when the submission lookup errors with no data.
 */
import * as React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionDetail, Submission } from "@arena/shared-types";

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

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace }),
  usePathname: () => "/workspace/session-1",
}));

vi.mock("@/lib/ws", () => ({
  createReconnectingSocket: () => ({
    send: vi.fn(),
    close: vi.fn(),
    status: () => "open" as const,
  }),
  buildWsUrl: (s: string) => s,
}));

vi.mock("@/components/workspace/PanelLayout", () => ({
  PanelLayout: () => <div data-testid="panel-layout" />,
}));
vi.mock("@/components/workspace/Terminal", () => ({
  Terminal: () => <div data-testid="terminal" />,
}));
vi.mock("@/components/workspace/CodeEditor", () => ({
  CodeEditor: () => <div data-testid="code-editor" />,
}));
vi.mock("@/components/workspace/DiffViewer", () => ({
  DiffViewer: () => <div data-testid="diff-viewer" />,
}));
vi.mock("@/components/workspace/AgentChat", () => ({
  AgentChat: () => <div data-testid="agent-chat" />,
}));
vi.mock("@/components/workspace/Timeline", () => ({
  Timeline: () => <div data-testid="timeline" />,
}));
vi.mock("@/components/workspace/TestPanel", () => ({
  TestPanel: () => <div data-testid="test-panel" />,
}));

const getSession = vi.fn();
const getSubmission = vi.fn();
const getFileTree = vi.fn();
const getDiff = vi.fn();
const getTimeline = vi.fn();
const getWsToken = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getSession: (...args: unknown[]) => getSession(...args),
    getSubmission: (...args: unknown[]) => getSubmission(...args),
    getFileTree: (...args: unknown[]) => getFileTree(...args),
    getDiff: (...args: unknown[]) => getDiff(...args),
    getTimeline: (...args: unknown[]) => getTimeline(...args),
    getWsToken: (...args: unknown[]) => getWsToken(...args),
  };
});

import { WorkspaceShell } from "@/components/workspace/WorkspaceShell";
import { ApiError } from "@/lib/api";

function buildGradedSession(): SessionDetail {
  return {
    id: "session-1",
    user_id: "user-1",
    mission_id: "auth-cookie-expiration",
    status: "graded",
    started_at: "2026-05-21T10:00:00Z",
    completed_at: null,
    sandbox_id: "sandbox-1",
    sandbox_driver: "docker",
    current_commit: null,
    agent_turns: 0,
    attempt_index: 1,
    score: null,
    mode: "self_study",
    integrity_signals_count: 0,
    ws_token: "ws-token",
    mission: {
      id: "auth-cookie-expiration",
      title: "Expired Session Cookie Still Grants Access",
      short_description: "x",
      difficulty: "intermediate",
      category: "auth",
      estimated_minutes: 35,
      skills_tested: ["auth"],
      failure_mode_id: "checks_presence_not_expiration",
      version: 1,
      published: true,
      kind: "standard",
      repo_pack: "fullstack-auth-demo",
      initial_commit: "abc",
      manifest_sha256: "x",
      brief: "Brief.",
      language_runtime: "node20",
      visible_tests: [],
      expected_context_required: [],
      expected_context_recommended: [],
      expected_diff_lines_p50: 18,
      repo_pack_id: "fullstack-auth-demo",
      language: "typescript",
      tags: [],
      status: "shipped",
      target_release_date: null,
    },
  };
}

function renderShell() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <WorkspaceShell sessionId="session-1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  replace.mockReset();
  getFileTree.mockResolvedValue([]);
  getDiff.mockResolvedValue({ unified_diff: "" });
  getTimeline.mockResolvedValue([]);
  getWsToken.mockResolvedValue({ token: "ws-fresh", ttl_seconds: 60 });
  getSession.mockResolvedValue(buildGradedSession());
});

describe("WorkspaceShell graded-submission error recovery", () => {
  it("renders a Retry affordance when the submission lookup errors", async () => {
    getSubmission.mockRejectedValue(new ApiError("boom", 500, null));
    renderShell();

    await waitFor(() =>
      expect(
        screen.getByText(/couldn't open the report/i),
      ).toBeInTheDocument(),
    );

    // Recoverable affordances are present…
    expect(
      screen.getByRole("button", { name: /Retry/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Back to missions/i }),
    ).toHaveAttribute("href", "/missions");

    // …and we never auto-redirected to the report.
    expect(replace).not.toHaveBeenCalledWith(expect.stringMatching(/^\/report\//));
    // The infinite spinner copy must NOT be shown.
    expect(screen.queryByText(/Taking you to your report/i)).not.toBeInTheDocument();
  });

  it("Retry refetches the submission and then redirects to the report", async () => {
    getSubmission
      .mockRejectedValueOnce(new ApiError("boom", 500, null))
      .mockResolvedValueOnce({ id: "sub-1", session_id: "session-1" } as Submission);
    renderShell();

    const retry = await screen.findByRole("button", { name: /Retry/i });
    fireEvent.click(retry);

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/report/sub-1"));
  });

  it("still redirects normally when the submission lookup succeeds", async () => {
    getSubmission.mockResolvedValue({
      id: "sub-1",
      session_id: "session-1",
    } as Submission);
    renderShell();

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/report/sub-1"));
    expect(screen.queryByText(/couldn't open the report/i)).not.toBeInTheDocument();
  });
});
