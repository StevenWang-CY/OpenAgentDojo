import type {
  AgentTurn,
  ApiErrorBody,
  CommandInput,
  CommandRun,
  ConsentKind,
  ConsentState,
  ContextSelection,
  CreateSessionInput,
  DataExport,
  DeleteAccountRequest,
  DeletionScheduledRead,
  DisplayNameUpdate,
  EmailChangeConfirm,
  EmailChangeRequest,
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
  SkillsCatalog,
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
  /**
   * Parsed ``Retry-After`` header value in seconds (only set on 429
   * responses). Lets callers render "try again in Ns" without poking at
   * the underlying fetch response.
   */
  readonly retryAfterSeconds: number | null;

  constructor(
    message: string,
    status: number,
    body: ApiErrorBody | null,
    retryAfterSeconds: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.retryAfterSeconds = retryAfterSeconds;
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
    // FE-audit fix — when the backend's DeletionLockMiddleware blocks a
    // mutating request because ``deletion_scheduled_at`` is set on the
    // caller, it returns 403 with ``{code: "deletion_scheduled",
    // scheduled_for}``. A stale tab that doesn't know about the lock would
    // otherwise show a generic "couldn't update" toast. Fire a window-level
    // event so the QueryClient host can invalidate ``["me"]`` once and
    // surface the lock banner; we still throw the ApiError so callers can
    // branch on it locally (e.g. close a dialog optimistically).
    if (
      response.status === 403 &&
      body?.code === "deletion_scheduled" &&
      typeof window !== "undefined"
    ) {
      try {
        window.dispatchEvent(
          new CustomEvent("deletion-lock-detected", { detail: body }),
        );
      } catch {
        // Defensive: older browsers / jsdom builds without CustomEvent
        // shouldn't break the API path.
      }
    }
    // Capture Retry-After on 429 so the rate-limit toast can render an
    // accurate "try again in Ns" hint instead of a generic 429.
    let retryAfterSeconds: number | null = null;
    if (response.status === 429) {
      const raw = response.headers.get("retry-after");
      if (raw !== null) {
        const parsed = Number.parseInt(raw, 10);
        if (Number.isFinite(parsed) && parsed >= 0) {
          retryAfterSeconds = parsed;
        }
      }
      // Fall back to the body's window_seconds if Retry-After was absent.
      if (
        retryAfterSeconds === null &&
        body &&
        typeof body.window_seconds === "number"
      ) {
        retryAfterSeconds = body.window_seconds;
      }
    }
    throw new ApiError(detail, response.status, body, retryAfterSeconds);
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
  /**
   * GET /api/v1/auth/me/consent — the caller's latest decision per kind.
   *
   * Returns the structural ``ConsentState`` shape: each of
   * ``analytics``/``functional``/``marketing`` is either a ``ConsentRecord``
   * or ``null`` (no row recorded yet). Requires authentication; anonymous
   * callers should not hit this — the cookie banner is the local-only
   * source of truth for unsigned-in users.
   */
  getConsent(signal?: AbortSignal): Promise<ConsentState> {
    return request<ConsentState>("/auth/me/consent", { signal });
  },
  /**
   * POST /api/v1/auth/me/consent — append a new consent row. The backend
   * stamps the server-side ``consent_policy_version`` itself; ``input`` is
   * just ``{kind, granted}``. 204 No Content on success.
   *
   * CSRF-required (handled transparently by the ``request`` wrapper). On
   * 401 the caller should silently no-op — the localStorage write is the
   * authoritative anonymous-path record.
   */
  setConsentRecord(
    input: { kind: ConsentKind; granted: boolean },
    signal?: AbortSignal,
  ): Promise<void> {
    return request<void>("/auth/me/consent", {
      method: "POST",
      body: input,
      signal,
    });
  },
};

// ── Account self-service (P0-6) ─────────────────────────────────────────────
//
// Every endpoint below sits under ``/api/v1/auth/me/*`` and requires the
// caller to be authenticated. The mutating endpoints are also guarded by the
// "deletion-scheduled lockout" middleware on the backend: while the caller's
// ``deletion_scheduled_at`` is set, every POST/PATCH/PUT/DELETE except
// ``/me/delete/cancel`` returns 403 ``{code: "deletion_scheduled",
// scheduled_for}``. The FE surfaces that state via ``DeletionLockBanner`` so
// the user understands why other actions are 403-ing.

export const account = {
  /**
   * PATCH /api/v1/auth/me — partial profile update.
   *
   * Only ``display_name`` is mutable; the backend rejects ``handle`` changes
   * at the schema layer (``extra="forbid"``) so attempting to send one yields
   * a typed 422, not a silent no-op. On success the response is the fresh
   * ``UserRead``; the caller should invalidate ``["me"]`` so the header and
   * any other surfaces re-render off the new value.
   */
  updateProfile(input: DisplayNameUpdate): Promise<User> {
    return request<User>("/auth/me", { method: "PATCH", body: input });
  },
  /**
   * POST /api/v1/auth/me/email/change — step 1 of the two-step email-change
   * flow. Sets ``users.pending_email`` to the requested address and emails a
   * magic-link with ``kind=email_change`` to the NEW address. 409 if the
   * target email is already in another account's ``email`` or
   * ``pending_email``. 204 on success — the FE renders the pending banner
   * from the next ``/me`` payload (which now carries ``pending_email``).
   */
  requestEmailChange(input: EmailChangeRequest): Promise<void> {
    return request<void>("/auth/me/email/change", {
      method: "POST",
      body: input,
    });
  },
  /**
   * POST /api/v1/auth/me/email/confirm — step 2 of the email-change flow.
   * The magic-link landing page (``/auth/email-confirm``) POSTs the token
   * here on mount. On success the backend atomically swaps ``email`` ⇄
   * ``pending_email``, rotates ``session_epoch`` (signing out every other
   * device), and mints a fresh session cookie for the caller. Returns the
   * refreshed ``UserRead``.
   *
   * Accepts an ``AbortSignal`` so the email-confirm page can cancel the
   * in-flight POST on unmount — otherwise navigating away mid-flight leaks
   * the request and the cookie-swap can race the new page's auth state.
   */
  confirmEmailChange(
    input: EmailChangeConfirm,
    signal?: AbortSignal,
  ): Promise<User> {
    return request<User>("/auth/me/email/confirm", {
      method: "POST",
      body: input,
      signal,
    });
  },
  /**
   * POST /api/v1/auth/me/sessions/sign-out-all — invalidate every existing
   * session by rotating ``session_epoch`` and re-mint a fresh cookie for the
   * caller. The caller stays signed-in on the current device. 204.
   */
  signOutAll(): Promise<void> {
    return request<void>("/auth/me/sessions/sign-out-all", { method: "POST" });
  },
  /**
   * POST /api/v1/auth/me/data-export — kick an async export job. 202 with the
   * ``DataExportRead`` envelope (``status: 'queued'``). 409 ``one-in-flight``
   * if a queued/running export already exists — the FE shows the existing
   * export instead of allowing a duplicate.
   */
  requestExport(): Promise<DataExport> {
    return request<DataExport>("/auth/me/data-export", { method: "POST" });
  },
  /**
   * GET /api/v1/auth/me/data-export/{id} — poll a specific export's status.
   * The ``DataExportPanel`` polls this every 2s with a 60-poll ceiling
   * (2 minutes — long enough for typical bundles, short enough that a stuck
   * worker doesn't burn battery forever). ``download_url`` is populated only
   * when ``status='ready'`` and the export hasn't expired.
   */
  getExport(id: string, signal?: AbortSignal): Promise<DataExport> {
    return request<DataExport>(
      `/auth/me/data-export/${encodeURIComponent(id)}`,
      { signal },
    );
  },
  /**
   * POST /api/v1/auth/me/delete — schedule account deletion on a 7-day grace
   * timer. Body re-types the user's email to confirm intent (GitHub-style
   * friction). On success the backend stamps
   * ``users.deletion_scheduled_at = now() + 7 days``, sends a confirmation
   * email with a cancel link, and returns ``{scheduled_for}``. Subsequent
   * POSTs (except ``/me/delete/cancel``) start returning 403 with
   * ``code: "deletion_scheduled"`` until the user cancels or the grace
   * elapses.
   */
  scheduleDeletion(
    input: DeleteAccountRequest,
  ): Promise<DeletionScheduledRead> {
    return request<DeletionScheduledRead>("/auth/me/delete", {
      method: "POST",
      body: input,
    });
  },
  /**
   * POST /api/v1/auth/me/delete/cancel — cancel a pending deletion during
   * the 7-day grace. 204 on success. 410 if the grace has already elapsed
   * (the daily worker has hard-deleted the account); the caller cannot
   * recover and the FE should sign them out.
   */
  cancelDeletion(): Promise<void> {
    return request<void>("/auth/me/delete/cancel", { method: "POST" });
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
  // ``input`` already shapes ``{mission_id, previous_session_id?}`` per the
  // backend ``SessionCreate`` schema (regenerated into shared-types/api.gen.ts).
  // The Retry CTA on the report page passes both fields so the new attempt
  // links back to its predecessor (P0-3 audit trail).
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

/**
 * POST /api/v1/sessions/{id}/give-up — forfeit the session, reveal the ideal
 * solution, and cap the score at 50/100 (P0-4).
 *
 * Behaviour:
 *  - Emits a ``session.gave_up`` supervision event so the timeline reflects
 *    the deliberate forfeit.
 *  - Stamps ``sessions.gave_up_at`` and immediately hands off to the
 *    standard submit pipeline (same path as ``submitSession``).
 *  - Returns the resulting ``Submission`` with ``score_cap_reason="gave_up"``.
 *
 * Errors the caller must surface:
 *  - ``425 Too Early`` — the 10-minute soft block hasn't elapsed yet.
 *    ``error.body.detail.seconds_remaining`` carries the wait time so the
 *    FE can render a countdown.
 *  - ``409 session_not_active`` — the session isn't ``active`` (already
 *    submitting / graded / abandoned / errored).
 */
export function giveUpSession(sessionId: string): Promise<Submission> {
  return request<Submission>(
    `/sessions/${encodeURIComponent(sessionId)}/give-up`,
    { method: "POST" },
  );
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

/** P2-3. GET /api/v1/profiles/me/skills — per-failure-mode mastery for the
 *  logged-in user. Requires auth. */
export function getMySkills(signal?: AbortSignal): Promise<SkillsCatalog> {
  return request<SkillsCatalog>(`/profiles/me/skills`, { signal });
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

// ── P0-1 — Tutorial endpoints ───────────────────────────────────────────────

/** POST /api/v1/sessions/{id}/events/tutorial-step — record a coachmark
 *  step transition. The backend persists the event to the supervision log;
 *  the grader ignores it (tutorial missions short-circuit scoring).
 *  204 No Content on success. */
export function markTutorialStep(
  sessionId: string,
  stepId: string,
  action: "completed" | "dismissed" = "completed",
): Promise<void> {
  return request<void>(
    `/sessions/${encodeURIComponent(sessionId)}/events/tutorial-step`,
    { method: "POST", body: { step_id: stepId, action } },
  );
}

/** POST /api/v1/auth/me/tutorial/replay — clear tutorial completion + bump
 *  the replay count. Returns the refreshed User so the header dropdown and
 *  catalog banner re-render off the latest /me. */
export function replayTutorial(): Promise<User> {
  return request<User>(`/auth/me/tutorial/replay`, { method: "POST" });
}
