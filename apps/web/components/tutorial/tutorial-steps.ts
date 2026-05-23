/**
 * P0-1 — Mission 00 coachmark sequence.
 *
 * Six steps, each anchored to a DOM ref already present in the workspace
 * shell. Each step is a pure function of (events, currentStep): the
 * ``shouldAdvance`` predicate inspects the latest supervision event stream
 * and decides whether to flip to the next coachmark. No polling, no
 * imperative side-effects from the steps themselves — the controller is
 * what fires the network call.
 *
 * The wording is short and second-person on purpose; the tutorial is a
 * guided tour, not documentation.
 */

import type { SupervisionEvent } from "@arena/shared-types";

/** Anchor key — maps to a DOM ref the workspace registers via
 *  `data-tutorial-anchor=…`. Adding a new step here without a matching
 *  data attribute will fall back to the page centre. */
export type TutorialAnchor =
  | "file-tree"
  | "agent-chat"
  | "apply-patch"
  | "diff-tab"
  | "test-panel"
  | "submit-button";

export interface TutorialStep {
  /** Stable kebab-case identifier persisted via the
   *  ``/events/tutorial-step`` endpoint so analytics + replay can pin
   *  events to a specific step. */
  id: string;
  /** Sequence position (1-based). */
  index: number;
  /** Visible heading. */
  title: string;
  /** Coachmark body copy — one or two sentences, plain text. */
  body: string;
  /** Where the popover anchors. */
  anchor: TutorialAnchor;
  /** Pure predicate over the event stream — returns true when the step
   *  should auto-advance. The controller still requires the user to
   *  click "got it" if they want; advance is the floor, not the cap. */
  shouldAdvance: (events: SupervisionEvent[]) => boolean;
}

function hasEventOfType(events: SupervisionEvent[], type: string): boolean {
  return events.some((e) => e.event_type === type);
}

/**
 * Step list — the same shape the post-mortem walkthrough's narrative
 * relies on. Adding a new step requires:
 *   1. Add a TutorialStep entry here.
 *   2. Add a matching `data-tutorial-anchor=…` somewhere in the
 *      workspace shell so Coachmark can position itself.
 *   3. Update Mission 00's brief if the new step changes the flow.
 */
export const TUTORIAL_STEPS: readonly TutorialStep[] = [
  {
    id: "select-context",
    index: 1,
    title: "Select the files the agent should read",
    body:
      "Tick at least one file in the tree. Context selection is scored " +
      "— a bad pick sends the agent down the wrong rabbit hole.",
    anchor: "file-tree",
    shouldAdvance: (events) => {
      const ctx = events.find((e) => e.event_type === "context.selected");
      if (!ctx) return false;
      const payload = ctx.payload as { files?: string[] };
      return (payload?.files?.length ?? 0) >= 1;
    },
  },
  {
    id: "prompt-the-agent",
    index: 2,
    title: "Write a prompt that names the bug",
    body:
      "Ask the agent to fix the welcome banner. A strong prompt names the " +
      "file, says what to check, and asks for a regression test.",
    anchor: "agent-chat",
    shouldAdvance: (events) =>
      events.some((e) => {
        if (e.event_type !== "prompt.submitted") return false;
        const payload = e.payload as { text?: string };
        return (payload?.text?.length ?? 0) >= 20;
      }),
  },
  {
    id: "apply-patch",
    index: 3,
    title: "Read the agent's narration, then apply",
    body:
      "The agent will propose a patch. Read what it says BEFORE pressing " +
      "Apply — narration is plausible, the diff can still be wrong.",
    anchor: "apply-patch",
    shouldAdvance: (events) => hasEventOfType(events, "patch.applied"),
  },
  {
    id: "open-the-diff",
    index: 4,
    title: "Open the diff and read every changed line",
    body:
      "Switch to the diff tab. The agent's patch will look defensible. " +
      "Ask yourself: did it actually fix the bug you described?",
    anchor: "diff-tab",
    shouldAdvance: (events) => hasEventOfType(events, "diff.opened"),
  },
  {
    id: "verify-with-tests",
    index: 5,
    title: "Run the tests",
    body:
      "Verification is the highest-leverage habit. Click the Test panel " +
      "and run the visible suite at least once — passing is necessary " +
      "but not sufficient (hidden tests catch the rest).",
    anchor: "test-panel",
    // Only test/typecheck commands satisfy this step. The previous
    // fallback ``|| eventCount(events, "command.run") >= 1`` advanced on
    // ANY command (``pnpm install``, ``ls``…) which defeated the lesson
    // the step is supposed to teach.
    shouldAdvance: (events) =>
      events.some((e) => {
        if (e.event_type !== "command.run") return false;
        const payload = e.payload as { category?: string };
        return payload?.category === "test" || payload?.category === "typecheck";
      }),
  },
  {
    id: "submit",
    index: 6,
    title: "Submit when you're ready",
    body:
      "Press Submit. The next page shows what you did versus what was " +
      "expected — your post-mortem walkthrough.",
    anchor: "submit-button",
    shouldAdvance: (events) => hasEventOfType(events, "submission.requested"),
  },
] as const;

/** Resolve the next step to surface given the current event stream. Returns
 *  -1 once every step has auto-advanced (the controller renders nothing). */
export function nextStepIndex(
  events: SupervisionEvent[],
  dismissed: ReadonlySet<string>,
  startAt = 0,
): number {
  for (let i = startAt; i < TUTORIAL_STEPS.length; i += 1) {
    const step = TUTORIAL_STEPS[i]!;
    if (dismissed.has(step.id)) continue;
    if (step.shouldAdvance(events)) continue;
    return i;
  }
  return -1;
}
