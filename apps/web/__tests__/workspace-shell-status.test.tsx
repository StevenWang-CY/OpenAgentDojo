import * as React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
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
  // `WorkspaceShell` now reads `usePathname()` to build the `?next=` param
  // it forwards to /auth/sign-in on a 401 token refetch.
  usePathname: () => "/workspace/session-1",
}));

// Don't actually open a WebSocket in tests.
vi.mock("@/lib/ws", () => ({
  createReconnectingSocket: () => ({
    send: vi.fn(),
    close: vi.fn(),
    status: () => "open" as const,
  }),
  buildWsUrl: (s: string) => s,
}));

// Replace the heavy nested components with sentinel divs so the test focuses
// purely on the status-branch routing.
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
vi.mock("@/components/workspace/SubmitDialog", () => ({
  SubmitDialog: () => <button>Submit</button>,
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

function buildSession(status: SessionDetail["status"]): SessionDetail {
  return {
    id: "session-1",
    user_id: "user-1",
    mission_id: "auth-cookie-expiration",
    status,
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
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  replace.mockReset();
  getFileTree.mockResolvedValue([]);
  getDiff.mockResolvedValue({ unified_diff: "" });
  getTimeline.mockResolvedValue([]);
  getWsToken.mockResolvedValue({ token: "ws-fresh", ttl_seconds: 60 });
  getSubmission.mockResolvedValue({
    id: "sub-1",
    session_id: "session-1",
  } as Submission);
});

describe("WorkspaceShell status branches", () => {
  it("renders the provisioning state", async () => {
    getSession.mockResolvedValue(buildSession("provisioning"));
    renderShell();
    await waitFor(() =>
      expect(screen.getByText(/Provisioning your sandbox/i)).toBeInTheDocument()
    );
  });

  it("renders the active workspace shell", async () => {
    getSession.mockResolvedValue(buildSession("active"));
    renderShell();
    await waitFor(() =>
      expect(screen.getByTestId("panel-layout")).toBeInTheDocument()
    );
  });

  it("renders the submitting overlay", async () => {
    getSession.mockResolvedValue(buildSession("submitting"));
    renderShell();
    await waitFor(() =>
      expect(screen.getByText(/Grading your submission/i)).toBeInTheDocument()
    );
  });

  it("redirects on graded by routing to the submission report", async () => {
    getSession.mockResolvedValue(buildSession("graded"));
    renderShell();
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/report/sub-1")
    );
  });

  it("renders the abandoned message with a restart CTA", async () => {
    getSession.mockResolvedValue(buildSession("abandoned"));
    renderShell();
    await waitFor(() =>
      expect(screen.getByText(/abandoned/i)).toBeInTheDocument()
    );
    expect(
      screen.getByRole("link", { name: /Restart mission/i })
    ).toHaveAttribute("href", "/missions/auth-cookie-expiration");
  });

  it("renders the error state with a restart CTA", async () => {
    getSession.mockResolvedValue(buildSession("error"));
    renderShell();
    await waitFor(() =>
      expect(
        screen.getByText(/Something went wrong with this session/i)
      ).toBeInTheDocument()
    );
  });

});
