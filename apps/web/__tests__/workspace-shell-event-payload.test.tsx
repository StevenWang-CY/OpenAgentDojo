import * as React from "react";
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { SessionDetail } from "@arena/shared-types";
import type { ReconnectingSocketOptions } from "@/lib/ws";

/**
 * F6 — `synthesiseAgentTurn` must drop malformed `agent.responded` payloads
 * rather than push a junk row into the workspace store. We exercise the
 * code path end-to-end by capturing the `onMessage` handler installed by
 * `WorkspaceShell` on `createReconnectingSocket`, then synthesising a
 * malformed wire frame.
 */

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

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/workspace/session-1",
}));

// Capture the options passed to createReconnectingSocket so the test can
// invoke the message handler directly.
const lastSocketOptions: { current: ReconnectingSocketOptions | null } = {
  current: null,
};

vi.mock("@/lib/ws", () => ({
  createReconnectingSocket: (opts: ReconnectingSocketOptions) => {
    lastSocketOptions.current = opts;
    return {
      send: vi.fn(),
      close: vi.fn(),
      status: () => "open" as const,
    };
  },
  buildWsUrl: (s: string) => s,
}));

// Stub the heavy nested components.
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
import { useWorkspaceStore } from "@/stores/workspaceStore";

function buildSession(): SessionDetail {
  return {
    id: "session-1",
    user_id: "user-1",
    mission_id: "auth-cookie-expiration",
    status: "active",
    started_at: "2026-05-21T10:00:00Z",
    completed_at: null,
    sandbox_id: "sandbox-1",
    sandbox_driver: "docker",
    current_commit: null,
    agent_turns: 0,
  attempt_index: 1,
    score: null,
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
  lastSocketOptions.current = null;
  getFileTree.mockResolvedValue([]);
  getDiff.mockResolvedValue({ unified_diff: "" });
  getTimeline.mockResolvedValue([]);
  getWsToken.mockResolvedValue({ token: "ws-fresh", ttl_seconds: 60 });
  getSession.mockResolvedValue(buildSession());
});

describe("WorkspaceShell — malformed agent.responded payload", () => {
  it("does not push an AgentTurn into the store when payload is malformed", async () => {
    // Reset the store for this session id before mounting so we can assert
    // on a clean agentTurns array.
    const store = useWorkspaceStore("session-1");
    store.getState().reset();

    renderShell();

    // Wait for the WS effect to install our options-capturing mock.
    await waitFor(() => expect(lastSocketOptions.current).not.toBeNull());

    const opts = lastSocketOptions.current!;
    expect(typeof opts.onMessage).toBe("function");

    // Silence the console.warn from synthesiseAgentTurn so the test output
    // stays clean — the warning itself is part of the contract.
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    // Fire a malformed agent.responded frame: `turn_index` is a string,
    // `response_summary` is missing.
    opts.onMessage!({
      data: JSON.stringify({
        id: 42,
        session_id: "session-1",
        event_type: "agent.responded",
        occurred_at: "2026-05-21T10:00:00Z",
        payload: { turn_index: "nope" },
      }),
    } as MessageEvent);

    // The event itself is a valid SupervisionEvent envelope so it should be
    // stored (the timeline still wants to render "Agent responded"), but
    // the synthesised AgentTurn must NOT land in the chat history.
    expect(store.getState().agentTurns).toHaveLength(0);
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("malformed agent.responded payload"),
      expect.anything()
    );

    warnSpy.mockRestore();
  });
});
