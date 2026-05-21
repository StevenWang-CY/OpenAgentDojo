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
  /** Lowercased mailbox local-part with disambiguating suffix; backend will
   *  populate from M3 onwards. Optional today since `UserRead` doesn't ship
   *  it yet. */
  handle?: string;
  created_at: ISODateString;
  last_login_at: ISODateString | null;
  /** CSRF token rotated on every /me call; embedded for clients that prefer
   *  reading from JSON over reading the arena_csrf cookie. */
  csrf_token?: string;
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
  score: number;
  max: number;
  signals: string[];
}

export type ScoreBreakdown = Record<RubricDimension, ScoreDimension>;

export interface ScoreReport {
  total: number;
  dimensions: ScoreBreakdown;
  strengths: string[];
  weaknesses: string[];
  missed_failure_mode: boolean;
  badges_earned: string[];
}

export interface ValidatorResult {
  kind: string;
  passed: boolean;
  violations: string[];
  penalty: number;
  evidence?: { file?: string; line?: number; snippet?: string }[];
}

export interface TestResult {
  name: string;
  passed: boolean;
  duration_ms: number | null;
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

export interface PublicProfile {
  handle: string;
  display_name: string | null;
  joined_at: ISODateString;
  badges: EarnedBadge[];
  history: MissionHistoryItem[];
  radar_averages: Partial<Record<RubricDimension, number>>;
  total_missions: number;
  best_score: number | null;
}

// ── API error envelope ───────────────────────────────────────────────────────

/**
 * The backend uses three shapes for ``detail``:
 *  - ``string`` — most ``HTTPException(detail="…")`` calls.
 *  - ``[{ msg, type?, loc? }]`` — FastAPI's request-validation 422 envelope.
 *  - ``object`` — structured detail bodies (e.g. 409 from ``POST /sessions``
 *    carries ``{detail, code, active_session_id}``; see M8 §21).
 * Consumers should narrow with ``typeof`` / structural checks before reading
 * nested fields.
 */
export interface ApiErrorBody {
  detail:
    | string
    | { msg: string; type?: string; loc?: (string | number)[] }[]
    | Record<string, unknown>;
}
