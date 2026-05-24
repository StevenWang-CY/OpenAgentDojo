// AUTO-GENERATED bindings layer; src/api.gen.ts is regenerated from
// apps/api/openapi.json. The supervision-event payloads themselves are
// hand-authored because the backend serialises them as untyped `dict`
// (see `app/sessions/events.py::EventEmitter.emit`); audit this file
// whenever new `event_type=` strings are added to the backend.
//
// This file is the SOURCE OF TRUTH for WebSocket event payload shapes;
// the FastAPI side is expected to conform. See Phase 4.A contract.
//
// Canonical source: apps/api/app/sessions/events.py + IMPLEMENTATION_PLAN.md §6.2.

import type {
  ContextSelection,
  ISODateString,
  SandboxDriver,
  ScoreDimension,
  UUID,
} from "./api";

export const SupervisionEventType = {
  SessionStarted: "session.started",
  SessionErrored: "session.errored",
  SessionAbandoned: "session.abandoned",
  SessionProvisionFailed: "session.provision_failed",
  ContextSelected: "context.selected",
  PromptSubmitted: "prompt.submitted",
  AgentResponded: "agent.responded",
  PatchProposed: "patch.proposed",
  PatchApplied: "patch.applied",
  PatchFailed: "patch.failed",
  DiffOpened: "diff.opened",
  DiffHovered: "diff.hovered",
  FileEdited: "file.edited",
  FileReverted: "file.reverted",
  CommandRun: "command.run",
  TestRun: "test.run",
  ValidatorFlag: "validator.flag",
  SubmissionRequested: "submission.requested",
  SubmissionGraded: "submission.graded",
  SubmissionFailed: "submission.failed",
  // P0-1 — tutorial coachmark milestones. Persisted via
  // ``POST /sessions/{id}/events/tutorial-step``; ignored by the grader.
  TutorialStepCompleted: "tutorial.step_completed",
  TutorialDismissed: "tutorial.dismissed",
  TutorialCompleted: "tutorial.completed",
  // P0-4 — emitted by the give-up endpoint (stub plumbing landed under
  // ``apps/api/alembic/versions/0014_give_up.py``).
  SessionGaveUp: "session.gave_up",
  // P0-5 — consent transitions. These are *account-scoped*, not
  // session-scoped: they persist to a dedicated ``consent_events`` table
  // (NOT ``supervision_events``) because the parent record is the user,
  // not a session. The FE WS timeline never receives them; they live in
  // the enum so the cross-language event-type contract stays exhaustive.
  ConsentGranted: "consent.granted",
  ConsentRevoked: "consent.revoked",
} as const;

export type SupervisionEventType =
  (typeof SupervisionEventType)[keyof typeof SupervisionEventType];

// ── Payloads (one per event type) ────────────────────────────────────────────
// Payload shapes mirror docs/schemas/event.schema.json — keep these in lockstep.

export interface SessionStartedPayload {
  mission_id: string;
  initial_commit?: string;
  sandbox_driver?: SandboxDriver;
}

/** Lifecycle failure (provisioning a sandbox, sandbox crash, grading crash). */
export type SessionErrorStage = "provisioning" | "sandbox" | "grading";

export interface SessionErroredPayload {
  stage: SessionErrorStage;
  detail: string;
}

// Backend may emit values outside this union (e.g. "ws_disconnect"); kept
// as a plain string per the Phase 4.A contract.
export interface SessionAbandonedPayload {
  reason?: string;
}

/**
 * Emitted when the provisioning worker fails to bring a sandbox online
 * (manifest load timeout, repo-pack hydration crash, etc.). Distinct from
 * ``session.errored`` because the FE renders a separate "provision failed"
 * UX for this signal — see ``apps/api/app/workers/provision.py`` and
 * IMPLEMENTATION_PLAN.md §P1-B8.
 */
export interface SessionProvisionFailedPayload {
  reason: string;
  detail?: string;
}

export type ContextSelectedPayload = ContextSelection;

export interface PromptSubmittedPayload {
  turn_index: number;
  text: string;
  char_count: number;
  intent?: string;
  context_files?: string[];
}

export type AgentIntent = "fix" | "test" | "revise" | "narrow" | "unknown";

/**
 * `source` distinguishes the deterministic harness path from a real LLM
 * call so replays + scoring can attribute behaviour correctly. The backend
 * currently emits ``"deterministic"`` | ``"llm"`` but the union widens to
 * ``string`` so future sources (e.g. a cached response) don't break the
 * type at compile time.
 */
export interface AgentRespondedPayload {
  turn_index: number;
  response_summary: string;
  intent?: AgentIntent;
  llm_used?: boolean;
  source?: "deterministic" | "llm" | string;
  proposed_actions?: string[];
  turn_id?: UUID;
}

export interface PatchProposedPayload {
  turn_index: number;
  /** Path to the proposed patch artefact (e.g. "patches/turn-0.patch"). */
  patch_file: string;
  intent?: AgentIntent;
  turn_id?: UUID;
}

export interface PatchAppliedPayload {
  turn_index: number;
  /**
   * Number of files touched by the patch. Distinct from the REST
   * `PatchResult.files_changed` (which is the actual `string[]` of paths);
   * the event payload only carries the count to keep the WS frame small.
   */
  file_count: number;
  added: number;
  removed: number;
  turn_id?: UUID;
}

export interface PatchFailedPayload {
  turn_index: number;
  /** Human-readable reason (e.g. "merge conflict in foo.ts"). */
  error: string;
  /** Same semantics as `PatchAppliedPayload.file_count`. */
  file_count?: number;
  added?: number;
  removed?: number;
  turn_id?: UUID;
}

export interface DiffOpenedPayload {
  /** Empty string ⇒ aggregate / workspace-wide diff. */
  path: string;
  /** Where in the UI the diff was opened from (e.g. "workspace", "report"). */
  surface?: string;
}

export interface DiffHoveredPayload {
  path: string;
  line?: number;
}

// Backend canonical values are ``"user"`` (typed in the workspace editor)
// and ``"agent"`` (written by an applied patch). The legacy alias
// ``"human"`` was never emitted; if it shows up on the wire it indicates a
// regressed producer.
export type FileEditSource = "user" | "agent";

export interface FileEditedPayload {
  path: string;
  added: number;
  removed: number;
  source?: FileEditSource;
}

export interface FileRevertedPayload {
  path: string;
}

// CommandCategory mirrors the REST `CommandBody.category` enum
// (see `apps/api/app/schemas/workspace.py::CommandCategory`). The
// "manual" bucket is what the workspace UI labels user-typed commands
// that don't fall into the test/typecheck/lint buckets.
export type CommandRunCategory =
  | "test"
  | "typecheck"
  | "lint"
  | "manual"
  | "other";

export interface CommandRunPayload {
  command: string;
  category: CommandRunCategory;
  exit_code?: number;
  duration_ms?: number;
}

export interface TestRunPayload {
  suite: string;
  passed: number;
  failed: number;
  skipped?: number;
  exit_code?: number;
}

export interface ValidatorFlagPayload {
  kind: string;
  message: string;
  /** Score penalty applied for this flag (if any). */
  penalty?: number;
  /** Short free-text evidence (e.g. "edited app/main.py:42"). */
  evidence?: string;
  /**
   * Populated by the prompt-injection detector (M8 §21,
   * ``apps/api/app/agent/router.py``): names of regex patterns that matched
   * (e.g. ``"ignore_previous_instructions"``). Only present when
   * ``kind === "prompt_injection"``.
   */
  patterns?: string[];
  /**
   * Banned-commands middleware (``apps/api/app/middleware/banned_commands.py``)
   * rides along extra diagnostic fields when ``kind === "banned_command"``:
   * - ``pattern``: the regex name that matched the blocked shell invocation
   * - ``command``: a truncated copy of the offending command line
   * The prompt-injection detector-error path also emits ``reason`` so the
   * grader can distinguish "check skipped" from a genuine match.
   */
  pattern?: string;
  command?: string;
  reason?: string;
}

export interface SubmissionRequestedPayload {
  /** ISO timestamp the session started — denormalised for grading runners. */
  started_at_iso?: string;
  diff_size_bytes?: number;
}

export interface SubmissionGradedPayload {
  /** Total score in [0, effective_max]. */
  score: number;
  /**
   * Per-dimension scores, keyed by `RubricDimension`. Each value is the
   * grader's per-dimension envelope (`{score, max, signals}`) so the
   * radar chart can render the score/max ratio without a second fetch.
   */
  breakdown: Record<string, ScoreDimension>;
  submission_id?: UUID;
  missed_failure_mode?: boolean;
  /** Badge ids awarded for this submission. Mirrors
   *  ``score_report.badges_earned`` so the Timeline can render a toast
   *  without a second fetch. */
  badges_earned?: string[];
  /** Effective denominator for the score (typically 100; drops to 90 when
   *  prompt_quality is pending, etc). Defaults to 100 server-side when
   *  absent so legacy consumers stay correct. */
  effective_max?: number;
}

export interface SubmissionFailedPayload {
  /** Pipeline stage that failed (e.g. "patch_apply", "tests", "scoring"). */
  stage: string;
  detail: string;
}

// ── P0-1 tutorial payloads ───────────────────────────────────────────────────

export interface TutorialStepCompletedPayload {
  step_id: string;
  mission_id: string;
}

export interface TutorialDismissedPayload {
  step_id: string;
  mission_id: string;
}

export interface TutorialCompletedPayload {
  mission_id: string;
  completed_at_iso: ISODateString;
}

// ── P0-4 give-up payload ─────────────────────────────────────────────────────

export interface SessionGaveUpPayload {
  seconds_into_session?: number;
}

// ── P0-5 consent payloads ────────────────────────────────────────────────────
//
// Account-scoped; not streamed over the per-session WS channel. Kept here
// so the cross-language event-type registry stays exhaustive (see
// ``apps/api/tests/test_event_contract.py``).

export type ConsentKind = "analytics" | "functional" | "marketing";

export interface ConsentGrantedPayload {
  kind: ConsentKind;
  version: number;
}

export interface ConsentRevokedPayload {
  kind: ConsentKind;
  version: number;
}

// ── Discriminated union ──────────────────────────────────────────────────────

export type SupervisionEvent =
  | SupervisionEventOf<"session.started", SessionStartedPayload>
  | SupervisionEventOf<"session.errored", SessionErroredPayload>
  | SupervisionEventOf<"session.abandoned", SessionAbandonedPayload>
  | SupervisionEventOf<"session.provision_failed", SessionProvisionFailedPayload>
  | SupervisionEventOf<"context.selected", ContextSelectedPayload>
  | SupervisionEventOf<"prompt.submitted", PromptSubmittedPayload>
  | SupervisionEventOf<"agent.responded", AgentRespondedPayload>
  | SupervisionEventOf<"patch.proposed", PatchProposedPayload>
  | SupervisionEventOf<"patch.applied", PatchAppliedPayload>
  | SupervisionEventOf<"patch.failed", PatchFailedPayload>
  | SupervisionEventOf<"diff.opened", DiffOpenedPayload>
  | SupervisionEventOf<"diff.hovered", DiffHoveredPayload>
  | SupervisionEventOf<"file.edited", FileEditedPayload>
  | SupervisionEventOf<"file.reverted", FileRevertedPayload>
  | SupervisionEventOf<"command.run", CommandRunPayload>
  | SupervisionEventOf<"test.run", TestRunPayload>
  | SupervisionEventOf<"validator.flag", ValidatorFlagPayload>
  | SupervisionEventOf<"submission.requested", SubmissionRequestedPayload>
  | SupervisionEventOf<"submission.graded", SubmissionGradedPayload>
  | SupervisionEventOf<"submission.failed", SubmissionFailedPayload>
  | SupervisionEventOf<"tutorial.step_completed", TutorialStepCompletedPayload>
  | SupervisionEventOf<"tutorial.dismissed", TutorialDismissedPayload>
  | SupervisionEventOf<"tutorial.completed", TutorialCompletedPayload>
  | SupervisionEventOf<"session.gave_up", SessionGaveUpPayload>
  | SupervisionEventOf<"consent.granted", ConsentGrantedPayload>
  | SupervisionEventOf<"consent.revoked", ConsentRevokedPayload>;

export interface SupervisionEventOf<
  T extends SupervisionEventType,
  P extends object,
> {
  id: number;
  session_id: UUID;
  event_type: T;
  payload: P;
  occurred_at: ISODateString;
}
