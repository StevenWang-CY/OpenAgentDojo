/**
 * P1 — on a live ``patch.applied`` event, WorkspaceShell must reconcile open
 * files: refetch the diff to learn which paths changed, drop their stale
 * persisted buffers (so the editor stops preferring localStorage over fresh
 * server content), and invalidate each ``["file", …]`` query so an open file
 * refetches. Previously only the tree + diff queries were invalidated and the
 * editor kept showing stale pre-patch content (even across reload, since
 * fileBuffers is persisted).
 */

import * as React from "react";
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { SessionDetail } from "@arena/shared-types";
import type { ReconnectingSocketOptions } from "@/lib/ws";

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

const CHANGED = "src/auth/session.ts";
const PATCH_DIFF = [
  `diff --git a/${CHANGED} b/${CHANGED}`,
  `--- a/${CHANGED}`,
  `+++ b/${CHANGED}`,
  "@@ -1 +1 @@",
  "-old",
  "+new",
  "",
].join("\n");

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
  return { client, ...render(
    <QueryClientProvider client={client}>
      <WorkspaceShell sessionId="session-1" />
    </QueryClientProvider>
  ) };
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

describe("WorkspaceShell — patch.applied reconciles open files", () => {
  it("clears the stale buffer for a changed path and refetches its diff", async () => {
    const store = useWorkspaceStore("session-1");
    store.getState().reset();
    // Pre-seed a stale (clean) buffer for the file the patch will change.
    store.getState().setActiveFileContent(CHANGED, "PRE-PATCH STALE");
    expect(store.getState().fileBuffers[CHANGED]).toBe("PRE-PATCH STALE");

    renderShell();

    await waitFor(() => expect(lastSocketOptions.current).not.toBeNull());
    const opts = lastSocketOptions.current!;

    // The post-patch diff now names the changed file.
    getDiff.mockResolvedValue({ unified_diff: PATCH_DIFF });
    const diffCallsBefore = getDiff.mock.calls.length;

    opts.onMessage!({
      data: JSON.stringify({
        id: 7,
        session_id: "session-1",
        event_type: "patch.applied",
        occurred_at: "2026-05-21T10:05:00Z",
        payload: { turn_index: 1, file_count: 1, added: 1, removed: 1 },
      }),
    } as MessageEvent);

    // The shell refetches the diff to learn the changed paths…
    await waitFor(() =>
      expect(getDiff.mock.calls.length).toBeGreaterThan(diffCallsBefore)
    );

    // …then drops the stale buffer so the editor falls back to fresh server
    // content for the changed file.
    await waitFor(() =>
      expect(store.getState().fileBuffers[CHANGED]).toBeUndefined()
    );
  });

  it("leaves buffers for untouched files intact", async () => {
    const store = useWorkspaceStore("session-1");
    store.getState().reset();
    store.getState().setActiveFileContent(CHANGED, "changed-buffer");
    store.getState().setActiveFileContent("README.md", "untouched-buffer");

    renderShell();
    await waitFor(() => expect(lastSocketOptions.current).not.toBeNull());
    const opts = lastSocketOptions.current!;
    getDiff.mockResolvedValue({ unified_diff: PATCH_DIFF });

    opts.onMessage!({
      data: JSON.stringify({
        id: 8,
        session_id: "session-1",
        event_type: "patch.applied",
        occurred_at: "2026-05-21T10:05:00Z",
        payload: { turn_index: 1, file_count: 1, added: 1, removed: 1 },
      }),
    } as MessageEvent);

    await waitFor(() =>
      expect(store.getState().fileBuffers[CHANGED]).toBeUndefined()
    );
    // The unrelated file's buffer survives.
    expect(store.getState().fileBuffers["README.md"]).toBe("untouched-buffer");
  });
});
