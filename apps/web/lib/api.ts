import type {
  AgentTurn,
  ApiErrorBody,
  CommandInput,
  CommandRun,
  ContextSelection,
  CreateSessionInput,
  FileContent,
  FileTreeNode,
  MagicLinkInput,
  Mission,
  MissionDetail,
  PatchResult,
  PromptInput,
  PublicProfile,
  Session,
  SessionDetail,
  Submission,
  SupervisionEvent,
  UnifiedDiff,
  User,
  WsTokenResponse,
} from "@arena/shared-types";
import { env } from "./env";

/**
 * NOTE — backend implementation status (auto-derived from `apps/api/openapi.json`):
 *
 *   Live in M2/M3:
 *     - GET  /missions
 *     - GET  /missions/{id}
 *     - POST /sessions                (202; provisioning is async)
 *     - GET  /sessions/{id}
 *     - GET  /sessions/{id}/ws-token
 *     - GET  /sessions/{id}/tree
 *     - GET  /sessions/{id}/file?path=
 *     - POST /sessions/{id}/files
 *     - POST /sessions/{id}/files/revert
 *     - POST /sessions/{id}/context
 *     - POST /sessions/{id}/commands
 *     - GET  /sessions/{id}/diff
 *     - GET  /sessions/{id}/timeline
 *     - WS   /ws/sessions/{id}/terminal
 *     - WS   /ws/sessions/{id}/events
 *
 *   M3+ (live):
 *     - POST /auth/magic-link
 *     - GET  /auth/callback
 *     - POST /auth/logout
 *     - GET  /auth/me              (also exposed at top-level `/me` alias)
 *     - POST /sessions/{id}/prompts
 *     - POST /sessions/{id}/patches/{turn_id}/apply
 *     - POST /sessions/{id}/submit
 *     - GET  /sessions/{id}/submission
 *     - GET  /reports/{submission_id}
 *     - POST /reports/{submission_id}/share
 *     - GET  /profiles/{handle}    (M7 — public profile, no auth required)
 *
 * Contract drift between the response types referenced here and
 * `apps/api/openapi.json` is enforced by `.github/workflows/contracts.yml`.
 */

/**
 * Typed error thrown by every `api.*` call. `status` is 0 for network/CORS
 * failures so consumers can distinguish offline-backend from 4xx/5xx.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly body: ApiErrorBody | null;

  constructor(message: string, status: number, body: ApiErrorBody | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/**
 * `unknown` (not a structural `Record<string, unknown>`) so callers can pass
 * any typed interface (`MagicLinkInput`, `CreateSessionInput`, …) without
 * TypeScript complaining about missing index signatures. `JSON.stringify`
 * still does the right thing at runtime.
 */
type JsonBody = unknown;

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: JsonBody;
  signal?: AbortSignal;
  query?: Record<string, string | number | boolean | undefined>;
}

/**
 * Read the non-HttpOnly `arena_csrf` cookie set by the backend. Returns
 * `null` on the server (no `document`) or when the cookie isn't set yet
 * (pre-login). A missing token is OK — the server-side guard responds with
 * 403 cleanly so callers don't need to short-circuit.
 */
export function getCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const cookies = document.cookie ? document.cookie.split(";") : [];
  for (const raw of cookies) {
    const trimmed = raw.trim();
    if (trimmed.startsWith("arena_csrf=")) {
      const value = trimmed.slice("arena_csrf=".length);
      try {
        return decodeURIComponent(value);
      } catch {
        return value;
      }
    }
  }
  return null;
}

/**
 * Endpoints exempted from the CSRF requirement by the backend (auth
 * bootstrap routes — the cookie may not be set yet on the very first
 * magic-link round-trip). Keep in lockstep with `apps/api/app/auth/csrf.py`.
 */
const CSRF_EXEMPT_PATHS = new Set<string>([
  "/auth/magic-link",
  "/auth/callback",
]);

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const url = new URL(`/api/v1${path}`, `${env.apiBaseUrl}/`);
  if (opts.query) {
    for (const [k, v] of Object.entries(opts.query)) {
      if (v !== undefined) url.searchParams.set(k, String(v));
    }
  }

  const method = opts.method ?? "GET";
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };

  // CSRF: only required on mutating methods. The backend exempts the
  // magic-link bootstrap so users without a cookie can still sign in.
  if (MUTATING_METHODS.has(method) && !CSRF_EXEMPT_PATHS.has(path)) {
    const token = getCsrfToken();
    if (token) {
      headers["X-Csrf-Token"] = token;
    }
    // A missing token is intentionally still sent — the backend returns
    // a structured 403 that surfaces as an ApiError, which is better than
    // a silent FE-side block.
  }

  let response: Response;
  try {
    response = await fetch(url.toString(), {
      method,
      credentials: "include",
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch {
    // Surface the full URL (origin + path + query) so the error pinpoints
    // exactly which backend host failed — invaluable when debugging local
    // env overrides vs. the deployed API.
    throw new ApiError(
      `Network error contacting ${url.toString()}`,
      0,
      null
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const payload: unknown = isJson
    ? await response.json().catch(() => null)
    : await response.text().catch(() => null);

  if (!response.ok) {
    const body = (isJson ? payload : null) as ApiErrorBody | null;
    const detail =
      body && typeof body.detail === "string"
        ? body.detail
        : `HTTP ${response.status}`;
    throw new ApiError(detail, response.status, body);
  }

  return payload as T;
}

// ── Auth / identity ─────────────────────────────────────────────────────────

export const auth = {
  /** POST /api/v1/auth/magic-link — sends a one-time login link. */
  sendMagicLink(input: MagicLinkInput): Promise<void> {
    return request<void>("/auth/magic-link", { method: "POST", body: input });
  },
  /** POST /api/v1/auth/logout — clears the session cookie. */
  logout(): Promise<void> {
    return request<void>("/auth/logout", { method: "POST" });
  },
  /**
   * GET /api/v1/auth/me — current authenticated user.
   *
   * Seam: IMPLEMENTATION_PLAN §12.1 sketches a top-level `/me` alias. The
   * backend (4.1) currently only exposes `/auth/me`; the alias would be a
   * pure `app.add_api_route` in `apps/api/app/main.py`. Until that lands,
   * the canonical path is the routed one, which is what we hit here.
   */
  me(signal?: AbortSignal): Promise<User> {
    return request<User>("/auth/me", { signal });
  },
};

// ── Missions ─────────────────────────────────────────────────────────────────

export function listMissions(signal?: AbortSignal): Promise<Mission[]> {
  return request<Mission[]>("/missions", { signal });
}

export function getMission(id: string, signal?: AbortSignal): Promise<MissionDetail> {
  return request<MissionDetail>(`/missions/${encodeURIComponent(id)}`, { signal });
}

// ── Sessions ─────────────────────────────────────────────────────────────────

export function createSession(input: CreateSessionInput): Promise<Session> {
  return request<Session>("/sessions", { method: "POST", body: input });
}

export function getSession(id: string, signal?: AbortSignal): Promise<SessionDetail> {
  return request<SessionDetail>(`/sessions/${encodeURIComponent(id)}`, { signal });
}

/**
 * Mint a short-lived (60s) HMAC token used to authenticate the terminal /
 * events WebSocket. The backend mirrors the same secret-derivation used
 * by `app.ws.auth.verify_ws_token`.
 */
export function getWsToken(
  sessionId: string,
  signal?: AbortSignal
): Promise<WsTokenResponse> {
  return request<WsTokenResponse>(
    `/sessions/${encodeURIComponent(sessionId)}/ws-token`,
    { signal }
  );
}

// ── Context / agent / files / commands ─────────────────────────────────────

/** POST /api/v1/sessions/{id}/context — record context selection. */
export function setContext(
  sessionId: string,
  context: ContextSelection
): Promise<void> {
  return request<void>(`/sessions/${encodeURIComponent(sessionId)}/context`, {
    method: "POST",
    body: context,
  });
}

/** POST /api/v1/sessions/{id}/prompts — submit a turn to the agent. */
export function submitPrompt(
  sessionId: string,
  input: PromptInput
): Promise<AgentTurn> {
  return request<AgentTurn>(`/sessions/${encodeURIComponent(sessionId)}/prompts`, {
    method: "POST",
    body: input,
  });
}

/** POST /api/v1/sessions/{id}/patches/{turn_id}/apply. */
export function applyPatch(
  sessionId: string,
  turnId: string
): Promise<PatchResult> {
  return request<PatchResult>(
    `/sessions/${encodeURIComponent(sessionId)}/patches/${encodeURIComponent(
      turnId
    )}/apply`,
    { method: "POST" }
  );
}

/** POST /api/v1/sessions/{id}/commands — run a shell command in the sandbox. */
export function runCommand(
  sessionId: string,
  input: CommandInput
): Promise<CommandRun> {
  return request<CommandRun>(`/sessions/${encodeURIComponent(sessionId)}/commands`, {
    method: "POST",
    body: input,
  });
}

/**
 * GET /api/v1/sessions/{id}/tree — workspace file tree.
 *
 * Seam: IMPLEMENTATION_PLAN §5.2 frames the response as a single root
 * `FileTreeNode`; the backend (4.1) currently returns the root's children
 * as a flat list because `apps/web/components/workspace/FileTree.tsx`
 * iterates an array. We mirror the wire shape exactly. If the backend
 * later promotes the root to a single object, regenerate `api.gen.ts`
 * (`pnpm --filter @arena/shared-types regen`) and update this return type
 * in lockstep.
 */
export function getFileTree(
  sessionId: string,
  signal?: AbortSignal
): Promise<FileTreeNode[]> {
  return request<FileTreeNode[]>(
    `/sessions/${encodeURIComponent(sessionId)}/tree`,
    { signal }
  );
}

/** GET /api/v1/sessions/{id}/file?path=… — read a single workspace file. */
export function getFile(
  sessionId: string,
  path: string,
  signal?: AbortSignal
): Promise<FileContent> {
  return request<FileContent>(`/sessions/${encodeURIComponent(sessionId)}/file`, {
    query: { path },
    signal,
  });
}

/** POST /api/v1/sessions/{id}/files — write a workspace file. */
export function writeFile(
  sessionId: string,
  path: string,
  content: string
): Promise<void> {
  return request<void>(`/sessions/${encodeURIComponent(sessionId)}/files`, {
    method: "POST",
    body: { path, content },
  });
}

/** POST /api/v1/sessions/{id}/files/revert — revert a file to last commit. */
export function revertFile(sessionId: string, path: string): Promise<void> {
  return request<void>(`/sessions/${encodeURIComponent(sessionId)}/files/revert`, {
    method: "POST",
    body: { path },
  });
}

/** GET /api/v1/sessions/{id}/diff — unified diff vs. initial_commit. */
export function getDiff(
  sessionId: string,
  signal?: AbortSignal
): Promise<UnifiedDiff> {
  return request<UnifiedDiff>(`/sessions/${encodeURIComponent(sessionId)}/diff`, {
    signal,
  });
}

/**
 * POST /api/v1/sessions/{id}/events/diff-opened — emits a `diff.opened`
 * supervision event. Called once by `DiffViewer` when the user first views a
 * non-empty diff; drives the "Agent Output Review" score dimension (§11.2.4).
 *
 * `path` is the file path the user opened (`""` for the workspace-wide
 * aggregate surface). Locked-in payload shape per the Phase 4.A contract:
 * `{ path: string }`.
 */
export function markDiffOpened(sessionId: string, path: string): Promise<void> {
  return request<void>(
    `/sessions/${encodeURIComponent(sessionId)}/events/diff-opened`,
    { method: "POST", body: { path } }
  );
}

/** GET /api/v1/sessions/{id}/timeline — historical supervision events. */
export function getTimeline(
  sessionId: string,
  signal?: AbortSignal
): Promise<SupervisionEvent[]> {
  return request<SupervisionEvent[]>(
    `/sessions/${encodeURIComponent(sessionId)}/timeline`,
    { signal }
  );
}

/** POST /api/v1/sessions/{id}/submit — freeze diff and start grading. */
export function submitSession(sessionId: string): Promise<Submission> {
  return request<Submission>(`/sessions/${encodeURIComponent(sessionId)}/submit`, {
    method: "POST",
  });
}

/** GET /api/v1/sessions/{id}/submission — read the graded submission. */
export function getSubmission(
  sessionId: string,
  signal?: AbortSignal
): Promise<Submission> {
  return request<Submission>(
    `/sessions/${encodeURIComponent(sessionId)}/submission`,
    { signal }
  );
}

// ── Reports / profiles ──────────────────────────────────────────────────────

/**
 * GET /api/v1/reports/{submission_id}.
 *
 * Pass `share` (the opaque token returned by `shareReport`) when the viewer is
 * unauthenticated — e.g. someone loading the report via a public share link.
 * The token is appended as `?share=…` so the backend can short-circuit the
 * normal session-owner check.
 */
export function getReport(
  submissionId: string,
  share?: string | null,
  signal?: AbortSignal
): Promise<Submission> {
  const query = share ? { share } : undefined;
  return request<Submission>(
    `/reports/${encodeURIComponent(submissionId)}`,
    { query, signal }
  );
}

/** M7+. GET /api/v1/profiles/{handle}. */
export function getProfile(
  handle: string,
  signal?: AbortSignal
): Promise<PublicProfile> {
  return request<PublicProfile>(`/profiles/${encodeURIComponent(handle)}`, {
    signal,
  });
}

/**
 * Response shape for `POST /api/v1/reports/{submission_id}/share` — mints (or
 * retrieves) a public share URL for a graded submission. The backend returns
 * an absolute URL so the canonical host stays authoritative.
 */
export interface ShareReportResponse {
  share_url: string;
  share_token: string;
  expires_at: string;
}

export function shareReport(submissionId: string): Promise<ShareReportResponse> {
  return request<ShareReportResponse>(
    `/reports/${encodeURIComponent(submissionId)}/share`,
    { method: "POST" }
  );
}
