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
  FileListResponse,
  FileTreeNode,
  MagicLinkInput,
  MagicLinkResendResponse,
  Mission,
  MissionDetail,
  PatchResult,
  PromptInput,
  PublicProfile,
  RecommendationSet,
  ReportRenderRead,
  SearchRequest,
  SearchResponse,
  Session,
  SessionDetail,
  SessionResetResponse,
  ShareTokenRead,
  SkillsCatalog,
  Submission,
  SupervisionEvent,
  UnifiedDiff,
  User,
  VerifyEnvelopeRead,
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
  /** Single-value query params. P1-1 added ``queryList`` for repeatable
   *  params (FastAPI ``list[str]`` collation) so the catalog filter can
   *  pass ``?tags=a&tags=b`` cleanly. */
  query?: Record<string, string | number | boolean | undefined>;
  queryList?: Record<string, string[] | undefined>;
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
  // P0-10 — resend endpoint also exempt; the user may not yet have a
  // CSRF cookie when they hit "didn't get the link?" (e.g. they opened
  // sign-in in an incognito session). Keep in lockstep with the
  // backend's CSRFMiddleware exempt list.
  "/auth/magic-link/resend",
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
  if (opts.queryList) {
    for (const [k, values] of Object.entries(opts.queryList)) {
      if (!values) continue;
      for (const v of values) {
        url.searchParams.append(k, v);
      }
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
  /**
   * POST /api/v1/auth/magic-link/resend — resend the magic link with a
   * 60-second per-email cooldown (P0-10).
   *
   * Returns ``{wait_seconds}`` — ``0`` when a fresh send proceeded,
   * positive when the call was suppressed because a previous send is
   * still in the cooldown window. The FE uses the value to render its
   * countdown timer.
   *
   * The endpoint deliberately responds with the same shape regardless
   * of whether the user exists so it cannot be used as an
   * account-existence oracle. Throwing on non-OK responses (i.e. the
   * caller getting a real error like 422) is delegated to the
   * underlying ``request`` wrapper.
   */
  async resendMagicLink(
    input: MagicLinkInput,
  ): Promise<MagicLinkResendResponse> {
    const body = await request<Partial<MagicLinkResendResponse> | null>(
      "/auth/magic-link/resend",
      { method: "POST", body: input },
    );
    // The backend always sends ``{wait_seconds}``; fall back to 0 so a
    // future contract drift can't crash the FE (the timer just won't
    // start, which surfaces in QA before it ships).
    const wait = typeof body?.wait_seconds === "number" ? body.wait_seconds : 0;
    return { wait_seconds: wait };
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

  // ── P0-7 — GitHub OAuth identity verification ─────────────────────────────
  //
  // OAuth requires a real top-level browser navigation (the redirect to
  // github.com sets a session for the user there, then redirects back to
  // our callback which sets our cookie). ``fetch`` is the wrong tool for
  // the start path — the FE must ``window.location.assign`` instead.
  // ``isGithubOAuthAvailable`` is a cheap pre-flight so the FE only ever
  // shows the button when the backend would honour the click.

  /**
   * GET /api/v1/auth/github/available — feature-flag probe.
   *
   * Returns ``true`` only when both ``GITHUB_OAUTH_CLIENT_ID`` and
   * ``GITHUB_OAUTH_CLIENT_SECRET`` are set on the backend. The FE polls
   * this on the sign-in page mount and hides the "Continue with GitHub"
   * button when it returns ``false`` so users never see a path that
   * would 503 on click. Errors are treated as "disabled" — failing
   * closed keeps the magic-link path as the obvious fallback.
   */
  async isGithubOAuthAvailable(signal?: AbortSignal): Promise<boolean> {
    try {
      const body = await request<{ enabled: boolean }>(
        "/auth/github/available",
        { signal },
      );
      return body?.enabled === true;
    } catch {
      // Fail closed — better to hide the button than to surface a path
      // the backend can't honour.
      return false;
    }
  },

  /**
   * Initiate the GitHub OAuth round-trip via a top-level browser
   * navigation.
   *
   * This is NOT a ``fetch`` — OAuth needs a real top-level navigation so
   * the browser carries the user's GitHub session cookies forward and so
   * the final redirect lands back on our origin with the new session
   * cookie. The function returns ``void`` immediately and the page
   * navigates as soon as the assignment runs.
   *
   * ``returnTo`` is an in-app relative path (must start with ``/``). The
   * backend validates it before storing in the state JWT; tainted values
   * are silently dropped and the callback falls back to ``/missions``.
   */
  startGithubOAuth(opts?: { returnTo?: string }): void {
    const base = new URL(env.apiBaseUrl);
    const url = new URL("/api/v1/auth/github/start", base.origin);
    if (opts?.returnTo) {
      url.searchParams.set("return_to", opts.returnTo);
    }
    if (typeof window !== "undefined") {
      window.location.assign(url.toString());
    }
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

/** P1-1 — optional catalog filters mirrored from the backend
 *  ``GET /api/v1/missions`` query params. Every field is server-side;
 *  the FE also runs the same filter set client-side for snappy chip
 *  interactions, but exposing the wire-shape keeps the hook future-proof
 *  (e.g. a deep link from a recommendation surface can prefetch the
 *  pre-filtered slice). */
export interface ListMissionsFilters {
  /** Filter to missions whose pack carries this language. Mirrors
   *  ``repo_packs.language``. */
  language?: "typescript" | "python" | "go";
  /** OR-within-the-param: a mission matches if it carries *any* of the
   *  supplied tags. Closed vocabulary (see
   *  ``apps/api/app/missions/manifest.py::_KNOWN_TAGS``). */
  tags?: string[];
  /** Exact ``repo_pack_id`` match. */
  repoPack?: string;
  /** When true, append dated ``coming_soon`` placeholders from
   *  ``apps/api/app/missions/roadmap.yaml`` to the response. */
  includeUpcoming?: boolean;
}

export function listMissions(
  signal?: AbortSignal,
  filters?: ListMissionsFilters,
): Promise<Mission[]> {
  const query: Record<string, string | undefined> = {};
  if (filters?.language) query.language = filters.language;
  if (filters?.repoPack) query.repo_pack = filters.repoPack;
  if (filters?.includeUpcoming) query.include = "upcoming";
  const queryList: Record<string, string[] | undefined> = {};
  if (filters?.tags && filters.tags.length > 0) {
    queryList.tags = filters.tags;
  }
  return request<Mission[]>("/missions", { signal, query, queryList });
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

// ── P0-9 — Find-in-files / repo-wide workspace search ──────────────────────
//
// Two endpoints back the quick-open palette + find-in-files panel. Both
// accept an ``AbortSignal`` so the FE can cancel in-flight requests as the
// user types (the palette debounces by 120ms; the panel cancels on every
// new query).

/**
 * GET /api/v1/sessions/{id}/files/list — workspace file paths (gitignore-aware).
 *
 * ``query`` does a substring filter server-side so the FE can ship a small
 * candidate set to the client-side fuzzy ranker without re-fetching every
 * keystroke. ``max`` caps the response (server hard-caps to 5000 regardless).
 * The response includes ``truncated`` so the palette can surface a
 * "// some files omitted" hint.
 */
export function getFileList(
  sessionId: string,
  opts?: { query?: string; max?: number; signal?: AbortSignal },
): Promise<FileListResponse> {
  const query: Record<string, string | number | undefined> = {};
  if (opts?.query) query.query = opts.query;
  if (opts?.max !== undefined) query.max = opts.max;
  return request<FileListResponse>(
    `/sessions/${encodeURIComponent(sessionId)}/files/list`,
    { query, signal: opts?.signal },
  );
}

/**
 * POST /api/v1/sessions/{id}/files/search — find-in-files via ripgrep.
 *
 * Errors the caller must surface:
 *   - 400 ``invalid_regex`` — the regex toggle is on and the pattern is
 *     malformed. ``error.body.detail.message`` carries the ripgrep error.
 *   - 504 ``search_timeout`` — the ripgrep subprocess exceeded the 10s
 *     wall-clock budget. The FE shows a friendly toast and lets the user
 *     refine the pattern.
 *
 * Side-effect: every successful call lands a ``command.run`` supervision
 * event so the grader can credit the supervisor for actually poking around
 * the workspace before prompting.
 */
export function searchFiles(
  sessionId: string,
  req: SearchRequest,
  opts?: { signal?: AbortSignal },
): Promise<SearchResponse> {
  return request<SearchResponse>(
    `/sessions/${encodeURIComponent(sessionId)}/files/search`,
    { method: "POST", body: req, signal: opts?.signal },
  );
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

// ── P0-8 — proctored-mode integrity signals ─────────────────────────────────

/** Kinds the backend accepts on ``POST /sessions/{id}/events/integrity``.
 *  Mirror of the Python ``IntegrityKind`` Literal — keep these in lockstep. */
export type IntegrityEventKind =
  | "tab.blurred"
  | "tab.focused"
  | "paste.large"
  | "focus.lost"
  | "proctored.violation";

/** Best-effort POST of an integrity signal. The endpoint accepts the
 *  call on self-study sessions but persists nothing (returns 204); on
 *  proctored sessions it persists + increments the counter; on rate
 *  limit it returns 429. Callers (the ``IntegritySignaller``) catch
 *  errors so the workspace UX never tilts on this supplementary
 *  channel. */
export async function postIntegrityEvent(
  sessionId: string,
  kind: IntegrityEventKind,
  payload: Record<string, unknown>,
): Promise<void> {
  try {
    await request<void>(
      `/sessions/${encodeURIComponent(sessionId)}/events/integrity`,
      { method: "POST", body: { kind, payload } },
    );
  } catch (err) {
    // 409 means the backend rejected the kind (e.g. honor mode bouncer
    // — though in practice the endpoint returns 204 for self-study).
    // Swallow it so the FE's listener stack never crashes the session.
    if (err instanceof ApiError && err.status === 409) {
      return;
    }
    throw err;
  }
}

// ── P0-12 — workspace reset ─────────────────────────────────────────────────
//
// Wire shape is generated from ``SessionResetResponse`` (backend Pydantic
// model). The re-export keeps the historical FE name on this layer.
export type { SessionResetResponse };

/**
 * POST /api/v1/sessions/{id}/reset — wipe the workspace back to the
 * mission's initial commit. Side-effects (server-side):
 *   1. ``git reset --hard <initial_commit>``.
 *   2. ``git clean -fd``.
 *   3. Emit ``session.reset`` supervision event with files_discarded.
 *   4. Insert a ``FileChange(source='revert', path='*')`` row.
 *
 * Errors the FE handles:
 *   - ``409 session_not_active`` — session isn't ``active``.
 *   - ``500 git_reset_failed`` — sandbox is unhealthy; surface as a toast.
 */
export function resetSession(sessionId: string): Promise<SessionResetResponse> {
  return request<SessionResetResponse>(
    `/sessions/${encodeURIComponent(sessionId)}/reset`,
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

// ── P0-11 — verification envelope + render pipeline ─────────────────────────
//
// The wire shapes live in ``@arena/shared-types`` (generated from
// ``apps/api/openapi.json``). The aliases below preserve the historical
// FE-facing names (``VerifyEnvelope`` / ``ReportRender``) so call-sites
// don't churn; the underlying contract is now sourced from the backend
// Pydantic model rather than a hand-authored copy that could drift.
export type VerifyEnvelope = VerifyEnvelopeRead;
export type ReportRender = ReportRenderRead;

/**
 * GET /api/v1/verify/{submission_id} — public, cacheable.
 *
 * Used by both the standalone /verify page and any agent that wants to
 * confirm a credential URL. No auth required.
 */
export function getVerifyEnvelope(
  submissionId: string,
  signal?: AbortSignal,
): Promise<VerifyEnvelope> {
  return request<VerifyEnvelope>(
    `/verify/${encodeURIComponent(submissionId)}`,
    { signal },
  );
}

/**
 * GET /api/v1/reports/{id}/render?kind=pdf|png — render lifecycle.
 *
 * The endpoint returns ``302`` to a 5-minute signed URL when the render
 * is ready, ``202`` with a body for queued/running, and ``503`` for
 * failed. We send ``redirect: "manual"`` so the FE can distinguish
 * "ready, navigate the user to the signed URL" from "still in flight,
 * keep polling." When ``ready: true`` we return the render-endpoint
 * URL itself; the browser's next request follows the server's 302
 * transparently because the FE just sets ``window.location.href`` to
 * the same path.
 */
export interface RenderStatusReady {
  ready: true;
  url: string;
}

export async function getReportRenderStatus(
  submissionId: string,
  kind: "pdf" | "png",
  share?: string | null,
  signal?: AbortSignal,
): Promise<ReportRender | RenderStatusReady> {
  const params = new URLSearchParams();
  params.set("kind", kind);
  if (share) params.set("share", share);
  const renderUrl = new URL(
    `/api/v1/reports/${encodeURIComponent(submissionId)}/render?${params.toString()}`,
    `${env.apiBaseUrl}/`,
  ).toString();

  const resp = await fetch(renderUrl, {
    method: "GET",
    credentials: "include",
    redirect: "manual",
    signal,
  });
  // Browsers normalise a manual-mode 302 to ``type: "opaqueredirect"``
  // (status 0). We never see the Location header in JS, but the route
  // is keyed by submission_id + kind so we can simply navigate the user
  // to the same endpoint and let the redirect happen transparently in
  // the new top-level GET (with cookies attached).
  //
  // IMPORTANT: ``status === 0`` on its own is NOT a ready signal — it is
  // also produced by CORS failures and network errors. Only treat the
  // explicit opaqueredirect (or the rare same-origin 302 the browser
  // surfaces directly) as ready; everything else with status 0 is a
  // network failure and must throw so callers can show a toast.
  if (resp.type === "opaqueredirect" || resp.status === 302) {
    return { ready: true, url: renderUrl };
  }
  if (resp.type === "error" || resp.status === 0) {
    throw new ApiError(
      "network failure while polling render status",
      0,
      null,
    );
  }
  if (resp.status === 202 || resp.status === 503) {
    return (await resp.json()) as ReportRender;
  }
  if (resp.status >= 400) {
    let body: ApiErrorBody | null = null;
    try {
      body = (await resp.json()) as ApiErrorBody;
    } catch {
      // ignore; body remains null
    }
    throw new ApiError(
      `Render endpoint returned ${resp.status}`,
      resp.status,
      body,
    );
  }
  throw new ApiError("unexpected render-endpoint response", resp.status, null);
}

/**
 * POST /api/v1/reports/{id}/render — force a re-render. Owner only.
 */
export function forceReportRender(
  submissionId: string,
  kind: "pdf" | "png",
): Promise<ReportRender> {
  return request<ReportRender>(
    `/reports/${encodeURIComponent(submissionId)}/render`,
    { method: "POST", body: { kind } },
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
 * P1-2 — adaptive next-mission recommendation set for the signed-in user.
 *
 * Returns the deterministic top-3 ranking plus the LLM-polished diagnosis
 * + per-mission ``why`` prose. Requires auth (401 for anonymous callers);
 * the FE branches on the viewer's signed-in state before calling so this
 * endpoint never fires for unauthenticated catalog visitors.
 *
 * The cache hit/miss flag is surfaced as ``cache_hit`` on the body so
 * call-sites can attribute warm-vs-cold timing to the operational metric;
 * the deterministic fallback (when Bedrock is down) is indistinguishable
 * from a cache hit on the wire — the strings are templated, not blank.
 */
export function getMyRecommendations(
  signal?: AbortSignal,
): Promise<RecommendationSet> {
  return request<RecommendationSet>(`/me/recommendations`, { signal });
}

/**
 * Response shape for `POST /api/v1/reports/{submission_id}/share` — mints (or
 * retrieves) a public share URL for a graded submission. The backend returns
 * an absolute URL so the canonical host stays authoritative.
 *
 * Wire shape sourced from the generated ``ShareTokenRead`` (Pydantic model).
 */
export type ShareReportResponse = ShareTokenRead;

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
