"use client";

import * as React from "react";
import type { SupervisionEvent } from "@arena/shared-types";
import { markTutorialStep } from "@/lib/api";
import { Coachmark } from "./Coachmark";
import { TUTORIAL_STEPS, nextStepIndex } from "./tutorial-steps";

interface TutorialControllerProps {
  sessionId: string;
  events: SupervisionEvent[];
  /** When false, the controller renders nothing (the workspace shell
   *  mounts it unconditionally — gating happens here). */
  enabled: boolean;
}

/**
 * P0-1 — orchestrates the orientation coachmark sequence.
 *
 * Pure event-driven: re-derives the active step on every event push.
 * Step transitions emit ``tutorial.step_completed`` events server-side
 * so the post-mission report can show the user's exact path; user
 * dismissals emit ``tutorial.dismissed`` so content tuning can see
 * which steps were skipped.
 *
 * Behaviour:
 *   * enabled === false           → render null
 *   * no events match any step    → render step 1
 *   * latest step advanced        → POST tutorial.step_completed, render next
 *   * user dismisses               → POST tutorial.dismissed, render next
 *   * every step advanced/dismissed → render null (tutorial done)
 */
export function TutorialController({
  sessionId,
  events,
  enabled,
}: TutorialControllerProps) {
  const [dismissed, setDismissed] = React.useState<Set<string>>(
    () => new Set(),
  );
  const reportedRef = React.useRef<Set<string>>(new Set());

  const currentIndex = enabled
    ? nextStepIndex(events, dismissed, 0)
    : -1;

  // Whenever the derived step changes "forward" (auto-advance), emit the
  // completion event for the step we just left. Dedupe so a network
  // hiccup that re-mounts the controller doesn't replay every step.
  React.useEffect(() => {
    if (!enabled) return;
    if (currentIndex < 0) {
      // All steps cleared — emit completion for the last unreported step.
      for (const step of TUTORIAL_STEPS) {
        if (reportedRef.current.has(step.id)) continue;
        if (dismissed.has(step.id)) continue;
        if (!step.shouldAdvance(events)) continue;
        reportedRef.current.add(step.id);
        void markTutorialStep(sessionId, step.id, "completed").catch(() => {
          // Swallow — the tutorial is best-effort, not load-bearing.
        });
      }
      return;
    }
    // Report every step that advanced BEFORE the current one.
    for (let i = 0; i < currentIndex; i += 1) {
      const step = TUTORIAL_STEPS[i]!;
      if (reportedRef.current.has(step.id)) continue;
      if (!step.shouldAdvance(events)) continue;
      reportedRef.current.add(step.id);
      void markTutorialStep(sessionId, step.id, "completed").catch(() => {});
    }
  }, [currentIndex, dismissed, enabled, events, sessionId]);

  if (!enabled || currentIndex < 0) return null;
  const step = TUTORIAL_STEPS[currentIndex];
  if (!step) return null;

  const handleDismiss = () => {
    setDismissed((prev) => {
      const next = new Set(prev);
      next.add(step.id);
      return next;
    });
    void markTutorialStep(sessionId, step.id, "dismissed").catch(() => {});
  };

  const handleContinue = () => {
    // "Got it" advances the visible coachmark to the next non-advanced
    // step without emitting a "dismissed" event — the user is engaged,
    // they just want to move on before the auto-advance condition fires.
    // We mark the step as dismissed locally so the controller flips
    // immediately; the server-side event log still captures the real
    // advance signal whenever the action actually happens.
    handleDismiss();
  };

  return (
    <Coachmark
      step={step}
      totalSteps={TUTORIAL_STEPS.length}
      onDismiss={handleDismiss}
      onContinue={handleContinue}
    />
  );
}
