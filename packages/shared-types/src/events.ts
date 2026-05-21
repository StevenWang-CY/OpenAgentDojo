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

export type ContextSelectedPayload = ContextSelection;

export interface PromptSubmittedPayload {
  turn_index: number;
  text: string;
  char_count: number;
  keyword_hits?: string[];
}

export type AgentIntent = "fix" | "test" | "revise" | "narrow" | "unknown";

export interface AgentRespondedPayload {
  turn_index: number;
  response_summary: string;
  intent?: AgentIntent;
  llm_used?: boolean;
}

export interface PatchProposedPayload {
  turn_index: number;
  /** Path to the proposed patch artefact (e.g. "patches/turn-0.patch"). */
  patch_file: string;
  intent?: AgentIntent;
}

export interface PatchAppliedPayload {
  turn_index: number;
  files_changed: number;
  added: number;
  removed: number;
}

export interface PatchFailedPayload {
  turn_index: number;
  /** Human-readable reason (e.g. "merge conflict in foo.ts"). */
  error: string;
  files_changed?: number;
  added?: number;
  removed?: number;
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

export type FileEditSource = "human" | "agent";

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
}

export interface SubmissionRequestedPayload {
  /** ISO timestamp the session started — denormalised for grading runners. */
  started_at_iso?: string;
  diff_size_bytes?: number;
}

export interface SubmissionGradedPayload {
  /** Total score in [0, 100]. */
  score: number;
  /**
   * Per-dimension scores, keyed by `RubricDimension`. Each value is the
   * grader's per-dimension envelope (`{score, max, signals}`) so the
   * radar chart can render the score/max ratio without a second fetch.
   */
  breakdown: Record<string, ScoreDimension>;
  submission_id?: UUID;
  missed_failure_mode?: boolean;
}

export interface SubmissionFailedPayload {
  /** Pipeline stage that failed (e.g. "patch_apply", "tests", "scoring"). */
  stage: string;
  detail: string;
}

// ── Discriminated union ──────────────────────────────────────────────────────

export type SupervisionEvent =
  | SupervisionEventOf<"session.started", SessionStartedPayload>
  | SupervisionEventOf<"session.errored", SessionErroredPayload>
  | SupervisionEventOf<"session.abandoned", SessionAbandonedPayload>
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
  | SupervisionEventOf<"submission.failed", SubmissionFailedPayload>;

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
