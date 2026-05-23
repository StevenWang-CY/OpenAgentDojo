"use client";

import * as React from "react";
import type {
  CriticalMoment,
  StrengthOrString,
  Submission,
  SupervisionEvent,
} from "@arena/shared-types";
import { CriticalMomentScrubber } from "./CriticalMomentScrubber";
import { ThreeWayDiff } from "./ThreeWayDiff";

interface PostMortemWalkthroughProps {
  submission: Submission;
  events: SupervisionEvent[];
  onScrollToEvent: (eventId: number) => void;
}

/**
 * P0-2 — assembles the three load-bearing pieces of the report's
 * training surface:
 *
 *   1. **Critical moment** scrubber — pinned at the top so the user's
 *      attention lands on the most actionable diagnostic first.
 *   2. **Three-way diff** — user's diff vs. ideal vs. (collapsible)
 *      agent's original patch.
 *
 * Per-dimension evidence + the timeline scroll-through are owned by the
 * parent ``ReportView`` because they reuse the existing breakdown grid.
 */
export function PostMortemWalkthrough({
  submission,
  events,
  onScrollToEvent,
}: PostMortemWalkthroughProps) {
  const moments: CriticalMoment[] = submission.critical_moments ?? [];

  const hasAnyTrainingSurface =
    moments.length > 0
    || (submission.ideal_solution_diff?.trim().length ?? 0) > 0
    || (submission.final_diff?.trim().length ?? 0) > 0;

  if (!hasAnyTrainingSurface) return null;

  return (
    <div className="grid gap-7" data-testid="post-mortem-walkthrough">
      {moments.length > 0 ? (
        <CriticalMomentScrubber
          moments={moments}
          events={events}
          onScrollToEvent={onScrollToEvent}
        />
      ) : null}
      <ThreeWayDiff
        userDiff={submission.final_diff ?? ""}
        idealDiff={submission.ideal_solution_diff ?? ""}
        agentPatchDiff={submission.agent_patch_diff ?? null}
      />
    </div>
  );
}

/** Coerce a strength/weakness wire entry into a strict EvidenceEntry. Useful
 *  in tests + downstream renderers that don't want the union complexity. */
export { asEvidenceEntry } from "@arena/shared-types";
export type { StrengthOrString };
