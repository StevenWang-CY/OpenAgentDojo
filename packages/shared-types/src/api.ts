// AUTO-GENERATED bindings layer; src/api.gen.ts is regenerated from
// apps/api/openapi.json. This file is the hand-curated re-export surface:
// the runtime contract types are sourced from the generated `components`
// map wherever possible, with a few hand-authored extensions for shapes
// that the backend OpenAPI either omits (e.g. /me has no response_model)
// or expresses too loosely (e.g. SubmissionRead's JSONB fields).
//
// To refresh after a backend schema change:
//   uv --project apps/api run python apps/api/scripts/dump_openapi.py
//   pnpm --filter @arena/shared-types regen
//
// Re-exports keep the original aliases stable so callers (`apps/web/lib/api.ts`,
// component props, etc.) do not have to follow openapi-typescript's
// component['schemas']['…'] indirection at every call site.

import type { components } from "./api.gen";
import type { SupervisionEvent } from "./events";

// ── Generated re-exports ────────────────────────────────────────────────────
//
// A handful of these tighten the optional/array shapes that openapi-typescript
// emits for Pydantic fields with ``Field(default_factory=list)``. The backend
// always returns those keys (the default factory runs at serialization time);
// surfacing them as required avoids forcing every call-site through `?? []`.

type GenMissionListItem = components["schemas"]["MissionListItem"];
type GenMissionDetail = components["schemas"]["MissionDetail"];
type GenAgentTurnResponse = components["schemas"]["AgentTurnResponse"];
type GenContextSelection = components["schemas"]["ContextSelection"];
type GenCommandRunResponse = components["schemas"]["CommandRunResponse"];

export type Mission = Omit<GenMissionListItem, "skills_tested"> & {
  skills_tested: string[];
};

export type MissionDetailGen = Omit<
  GenMissionDetail,
  | "skills_tested"
  | "visible_tests"
  | "expected_context_required"
  | "expected_context_recommended"
> & {
  skills_tested: string[];
  visible_tests: string[];
  expected_context_required: string[];
  expected_context_recommended: string[];
};

export type ContextSelection = Required<GenContextSelection>;

export type AgentTurn = Omit<
  GenAgentTurnResponse,
  "proposed_actions" | "selected_context"
> & {
  proposed_actions: string[];
  selected_context: ContextSelection;
};

export type CommandRun = Omit<GenCommandRunResponse, "category"> & {
  category: CommandCategory;
};

export type SessionRead = components["schemas"]["SessionRead"];
export type SessionDetailGen = components["schemas"]["SessionDetail"];
export type CreateSessionInput = components["schemas"]["SessionCreate"];

type GenPatchResult = components["schemas"]["PatchResult"];
export type PatchResult = Omit<GenPatchResult, "files_changed"> & {
  files_changed: string[];
};
export type CommandInput = components["schemas"]["CommandBody"];
export type FileContent = components["schemas"]["FileContent"];
export type FileTreeNode = components["schemas"]["FileTreeNodeSchema"];
export type UnifiedDiff = components["schemas"]["UnifiedDiff"];
export type SupervisionEventRead =
  components["schemas"]["SupervisionEventRead"];
export type SubmissionRead = components["schemas"]["SubmissionRead"];
export type WriteFileInput = components["schemas"]["FileWriteBody"];
export type RevertFileInput = components["schemas"]["FileRevertBody"];
export type MagicLinkInput = components["schemas"]["MagicLinkRequest"];
export type PromptInput = components["schemas"]["PromptBody"];

// ── Primitives ───────────────────────────────────────────────────────────────

export type ISODateString = string;
export type UUID = string;

export type Difficulty = MissionDetailGen["difficulty"];
export type MissionCategory = MissionDetailGen["category"];
export type SessionStatus = SessionRead["status"];
export type SandboxDriver = SessionRead["sandbox_driver"];
export type CommandCategory = CommandInput["category"];
export type FileEncoding = FileContent["encoding"];
export type FileKind = FileTreeNode["kind"];

// ── User (hand-authored — backend /me has no Pydantic response model yet) ───

export interface User {
  id: UUID;
  email: string;
  display_name: string | null;
  github_login: string | null;
  /** Lowercased mailbox local-part with disambiguating suffix. Backend
   *  schema (`UserRead.handle: str | None`) is nullable for legacy / not-yet-
   *  migrated rows — callers must handle the null branch. */
  handle: string | null;
  created_at: ISODateString;
  last_login_at: ISODateString | null;
  /** CSRF token issued on every /me call (re-uses the existing cookie when
   *  present — see `_build_me_response` on the backend). Always populated by
   *  the route; the FE can rely on it being present without a defensive
   *  branch. */
  csrf_token: string;
}

// ── Mission enrichment ──────────────────────────────────────────────────────
// MissionDetail in the wire format includes the brief, repo info, and
// expected-context arrays. Re-export under the historical alias so the
// frontend's existing import sites keep working.
export type MissionDetail = MissionDetailGen;

// ── Session enrichment ──────────────────────────────────────────────────────

export type Session = SessionRead;
export type SessionDetail = Omit<SessionDetailGen, "mission"> & {
  mission: MissionDetail;
};

/** Response of `GET /sessions/{id}/ws-token`. */
export interface WsTokenResponse {
  token: string;
  ttl_seconds: number;
}

// ── Submission & scoring (narrower than SubmissionRead's JSONB fields) ─────
// SubmissionRead carries the raw JSON columns from Postgres so the OpenAPI
// schema can't express them precisely. These narrower types describe the
// shape the grading engine actually emits.

export type RubricDimension =
  | "final_correctness"
  | "verification"
  | "agent_review"
  | "prompt_quality"
  | "context_selection"
  | "safety"
  | "diff_minimality";

export interface ScoreDimension {
  /** `null` when the dimension is pending measurement (e.g. prompt-quality
   *  judge cache cold + LLM unavailable). The frontend renders pending
   *  dimensions with a distinct "—" marker instead of a number. */
  score: number | null;
  max: number;
  signals: string[];
}

export type ScoreBreakdown = Record<RubricDimension, ScoreDimension>;

/** One per weak dimension. Tells the user WHY they scored low and WHAT to
 *  do next. Populated by the diagnostic narrative generator (P2-1). */
export interface Diagnostic {
  dimension: RubricDimension;
  score: number | null;
  max: number;
  /** Plain-English explanation of the most likely cause, derived from the
   *  raw scoring signals (e.g. "you submitted 12s after agent responded
   *  without opening the diff"). */
  cause: string;
  /** Concrete next-step recommendation, often suggesting other missions
   *  that target this dimension. */
  recommendation: string;
  recommended_mission_ids: string[];
}

export interface ScoreReport {
  total: number;
  dimensions: ScoreBreakdown;
  strengths: string[];
  weaknesses: string[];
  missed_failure_mode: boolean;
  badges_earned: string[];
  /** Per-dimension diagnostic + next-mission recommendations. Empty array
   *  when no weaknesses (the user nailed everything) or when the
   *  dimension-level signals were insufficient to derive a cause. */
  feedback_narrative?: Diagnostic[];
  /** Effective maximum total this report could have reached. 100 in the
   *  normal case; drops to 90 when prompt_quality is pending, etc. The FE
   *  should render the score as ``total / effective_max`` rather than
   *  hardcoding /100 (see apps/api/app/grading/score.py). */
  effective_max?: number;
}

export interface ValidatorResult {
  kind: string;
  passed: boolean;
  violations: string[];
  penalty: number;
  evidence?: { file?: string; line?: number; snippet?: string }[];
}

/** One entry in ``visible_test_results`` / ``hidden_test_results``.
 *  Matches the wire shape ``TestRunResult.to_dict()`` emits server-side
 *  (apps/api/app/grading/validators/tests_pass.py:26-35). The earlier
 *  ``{name, passed, duration_ms}`` shape was speculative and never
 *  matched the actual backend payload. */
export interface TestResult {
  suite: string;
  exit_code: number;
  stdout: string;
  stderr: string;
  passed: number;
  failed: number;
  skipped: number;
}

/** `GET /reports/{submission_id}` (and `GET /sessions/{id}/submission`)
 *  payload. Re-narrows the JSONB-typed fields in `SubmissionRead` against
 *  the grader's actual emit shapes (see `apps/api/app/grading/score.py`). */
export type Submission = Omit<
  SubmissionRead,
  | "visible_test_results"
  | "hidden_test_results"
  | "validator_results"
  | "score_report"
> & {
  visible_test_results: TestResult[];
  hidden_test_results: TestResult[];
  validator_results: ValidatorResult[];
  score_report: ScoreReport;
};

// ── Timeline ─────────────────────────────────────────────────────────────────

export type Timeline = SupervisionEvent[];

// ── Profile / badges (M7 — no backend route yet) ───────────────────────────

export interface Badge {
  id: string;
  title: string;
  description: string;
  icon: string;
}

export interface EarnedBadge extends Badge {
  earned_at: ISODateString;
  session_id: UUID | null;
}

export interface MissionHistoryItem {
  session_id: UUID;
  mission_id: string;
  mission_title: string;
  completed_at: ISODateString | null;
  score: number | null;
  difficulty: Difficulty;
}

/** One `(completed_at, score)` point on a per-dimension sparkline. */
export interface DimensionTrendPoint {
  completed_at: ISODateString;
  score: number;
}

/** Per-failure-mode mastery row in the skills catalog (P2-3). */
export interface FailureModeMastery {
  failure_mode: string;
  failure_mode_title: string | null;
  mission_ids: string[];
  mission_titles: string[];
  sessions_attempted: number;
  sessions_passed: number;
  avg_score: number | null;
  best_score: number | null;
  last_attempted_at: ISODateString | null;
}

/** `GET /api/v1/profiles/me/skills` payload. */
export interface SkillsCatalog {
  failure_modes: FailureModeMastery[];
  total_missions: number;
  total_failure_modes: number;
}

export interface PublicProfile {
  handle: string;
  display_name: string | null;
  joined_at: ISODateString;
  badges: EarnedBadge[];
  history: MissionHistoryItem[];
  radar_averages: Partial<Record<RubricDimension, number>>;
  /** Per-dimension chronological score trail for the longitudinal
   *  sparklines (P2-2). Oldest first. Pending scores are excluded — the
   *  trail only contains points the grader could actually measure. */
  dimension_trends?: Partial<Record<RubricDimension, DimensionTrendPoint[]>>;
  total_missions: number;
  best_score: number | null;
}

// ── API error envelope ───────────────────────────────────────────────────────

/**
 * The backend uses three shapes for ``detail``:
 *  - ``string`` — most ``HTTPException(detail="…")`` calls.
 *  - ``[{ msg, type?, loc? }]`` — FastAPI's request-validation 422 envelope.
 *  - ``object`` — structured detail bodies (e.g. 409 from ``POST /sessions``
 *    carries ``{code, message, active_session_id}``; see M8 §21).
 * Consumers should narrow with ``typeof`` / structural checks before reading
 * nested fields.
 *
 * Top-level extras (``code``, ``limit``, ``window_seconds``) are emitted by
 * the CSRF, rate-limit, and ArenaError envelopes alongside ``detail``. They
 * are optional so a regular ``HTTPException`` body still matches the type.
 */
export interface ApiErrorBody {
  detail:
    | string
    | { msg: string; type?: string; loc?: (string | number)[] }[]
    | Record<string, unknown>;
  /** Stable machine-readable error code (e.g. "csrf_invalid",
   *  "rate_limited", "session_not_active"). Optional so plain
   *  ``HTTPException(detail="…")`` payloads keep matching. */
  code?: string;
  /** Rate-limit budget that the bucket holds (only on 429 envelopes). */
  limit?: number;
  /** Rate-limit window in seconds (only on 429 envelopes). Pair with the
   *  ``Retry-After`` header for the human-facing wait time. */
  window_seconds?: number;
}
