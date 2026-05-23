/**
 * Session-route contract tests.
 *
 * Exercises a representative slice of the session API surface
 * (create / read / tree / file / diff / context / commands) to confirm
 * the response shapes the backend ships still round-trip cleanly through
 * `lib/api.ts`.
 */
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import type {
  CommandRun,
  FileContent,
  FileTreeNode,
  Session,
  SessionDetail,
  UnifiedDiff,
} from "@arena/shared-types";
import {
  createSession,
  getDiff,
  getFile,
  getFileTree,
  getSession,
  getWsToken,
  runCommand,
  setContext,
} from "@/lib/api";
import { API_BASE, expectShape, withContractServer } from "./_setup";

const sessionId = "11111111-1111-1111-1111-111111111111";
const userId = "22222222-2222-2222-2222-222222222222";

const sessionRow: Session = {
  id: sessionId,
  user_id: userId,
  mission_id: "auth-cookie-expiration",
  status: "provisioning",
  started_at: "2025-05-21T10:00:00Z",
  completed_at: null,
  sandbox_id: null,
  sandbox_driver: "local",
  current_commit: null,
  score: null,
  agent_turns: 0,
  attempt_index: 1,
};

const sessionDetail: SessionDetail = {
  ...sessionRow,
  status: "active",
  sandbox_id: "ctr_abc",
  current_commit: "abc123de",
  mission: {
    id: "auth-cookie-expiration",
    title: "Expired Session Cookie Still Grants Access",
    short_description: "",
    difficulty: "intermediate",
    category: "auth",
    estimated_minutes: 35,
    skills_tested: ["auth"],
    failure_mode_id: "checks_presence_not_expiration",
    repo_pack: "fullstack-auth-demo",
    initial_commit: "abc123de",
    manifest_sha256: "f".repeat(64),
    version: 1,
    published: true,
  kind: "standard",
    brief: "",
    language_runtime: "node20",
    visible_tests: [],
    expected_context_required: ["backend/auth/session.ts", "backend/middleware/requireAuth.ts"],
    expected_context_recommended: [],
    expected_diff_lines_p50: 18,
  },
  ws_token: "ws-token-xyz",
};

const treeRoot: FileTreeNode[] = [
  {
    path: "/workspace/backend",
    name: "backend",
    kind: "directory",
    children: [
      {
        path: "/workspace/backend/auth/session.ts",
        name: "session.ts",
        kind: "file",
        size: 1024,
      },
    ],
  },
];

const fileContent: FileContent = {
  path: "/workspace/backend/auth/session.ts",
  content: "export function isValid() { return true; }",
  encoding: "utf-8",
  truncated: false,
};

const commandRun: CommandRun = {
  id: "33333333-3333-3333-3333-333333333333",
  session_id: sessionId,
  command: "pnpm test:unit",
  category: "test",
  exit_code: 0,
  duration_ms: 1200,
  created_at: "2025-05-21T10:01:00Z",
  stdout: "all green",
  stderr: "",
  // stdio_truncated landed alongside the Wave 1 schema bump on the BE; the
  // FE consumes the flag to render a "(truncated)" pill next to the output
  // pane. Fixture defaults to false because this sample produced sub-cap output.
  stdio_truncated: false,
};

const unifiedDiff: UnifiedDiff = {
  unified_diff:
    "diff --git a/backend/auth/session.ts b/backend/auth/session.ts\n--- a/...\n+++ b/...\n",
};

withContractServer([
  http.post(`${API_BASE}/api/v1/sessions`, async ({ request }) => {
    const body = (await request.json()) as { mission_id: string };
    if (typeof body.mission_id !== "string") {
      return HttpResponse.json({ detail: "missing mission_id" }, { status: 422 });
    }
    return HttpResponse.json(sessionRow, { status: 202 });
  }),
  http.get(`${API_BASE}/api/v1/sessions/:id`, ({ params }) => {
    if (params.id !== sessionId) {
      return HttpResponse.json({ detail: "not found" }, { status: 404 });
    }
    return HttpResponse.json(sessionDetail);
  }),
  http.get(`${API_BASE}/api/v1/sessions/:id/ws-token`, () =>
    HttpResponse.json({ token: "ws-token-xyz", ttl_seconds: 60 })
  ),
  http.get(`${API_BASE}/api/v1/sessions/:id/tree`, () =>
    HttpResponse.json(treeRoot)
  ),
  http.get(`${API_BASE}/api/v1/sessions/:id/file`, ({ request }) => {
    const u = new URL(request.url);
    if (!u.searchParams.get("path")) {
      return HttpResponse.json({ detail: "path required" }, { status: 422 });
    }
    return HttpResponse.json(fileContent);
  }),
  http.post(`${API_BASE}/api/v1/sessions/:id/commands`, async ({ request }) => {
    const body = (await request.json()) as { command: string; category: string };
    if (!body.command) {
      return HttpResponse.json({ detail: "command required" }, { status: 422 });
    }
    return HttpResponse.json({ ...commandRun, command: body.command, category: body.category });
  }),
  http.post(`${API_BASE}/api/v1/sessions/:id/context`, () =>
    new HttpResponse(null, { status: 204 })
  ),
  http.get(`${API_BASE}/api/v1/sessions/:id/diff`, () =>
    HttpResponse.json(unifiedDiff)
  ),
]);

describe("sessions contract", () => {
  it("POST /sessions returns SessionRead (202)", async () => {
    const s = await createSession({ mission_id: "auth-cookie-expiration" });
    expectShape(s as unknown as Record<string, unknown>, [
      "id",
      "user_id",
      "mission_id",
      "status",
      "started_at",
      "sandbox_driver",
      "agent_turns",
    ]);
    expect(s.status).toBe("provisioning");
  });

  it("GET /sessions/{id} returns SessionDetail with embedded mission + ws_token", async () => {
    const detail = await getSession(sessionId);
    expectShape(detail as unknown as Record<string, unknown>, [
      "id",
      "mission",
      "ws_token",
    ]);
    expect(detail.mission.id).toBe("auth-cookie-expiration");
    expect(detail.ws_token).toBe("ws-token-xyz");
  });

  it("GET /sessions/{id}/ws-token returns { token, ttl_seconds }", async () => {
    const t = await getWsToken(sessionId);
    expect(t.token).toMatch(/^ws-token-/);
    expect(t.ttl_seconds).toBe(60);
  });

  it("GET /sessions/{id}/tree returns FileTreeNode[]", async () => {
    const nodes = await getFileTree(sessionId);
    expect(Array.isArray(nodes)).toBe(true);
    expect(nodes[0]!.kind).toBe("directory");
    expect(nodes[0]!.children?.[0]?.kind).toBe("file");
  });

  it("GET /sessions/{id}/file sends the path query and parses FileContent", async () => {
    const fc = await getFile(sessionId, "/workspace/backend/auth/session.ts");
    expectShape(fc as unknown as Record<string, unknown>, [
      "path",
      "content",
      "encoding",
      "truncated",
    ]);
    expect(fc.encoding).toBe("utf-8");
    expect(fc.truncated).toBe(false);
  });

  it("POST /sessions/{id}/commands returns CommandRunResponse", async () => {
    const cr = await runCommand(sessionId, { command: "pnpm typecheck", category: "typecheck" });
    expect(cr.command).toBe("pnpm typecheck");
    expect(cr.category).toBe("typecheck");
    expect(cr.exit_code).toBe(0);
  });

  it("POST /sessions/{id}/context resolves on 204", async () => {
    await expect(
      setContext(sessionId, { files: ["a.ts"], logs: [], tests: [], extras: [] })
    ).resolves.toBeUndefined();
  });

  it("GET /sessions/{id}/diff returns the unified diff envelope", async () => {
    const d = await getDiff(sessionId);
    expect(d.unified_diff).toContain("diff --git");
  });
});
