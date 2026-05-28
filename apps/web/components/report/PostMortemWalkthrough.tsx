"use client";

import * as React from "react";
import type {
  CriticalMoment,
  StrengthOrString,
  Submission,
  SupervisionEvent,
} from "@arena/shared-types";
import { CoachingReflection } from "./CoachingReflection";
import { CriticalMomentScrubber } from "./CriticalMomentScrubber";
import { ThreeWayDiff, type ThreeWayDiffHandle } from "./ThreeWayDiff";
import type { LoadBearingMoment } from "./LoadBearingLineMarker";

interface PostMortemWalkthroughProps {
  submission: Submission;
  events: SupervisionEvent[];
  onScrollToEvent: (eventId: number) => void;
  /**
   * P1-4 — true when the viewer owns the submission. Only owners see
   * the coaching reflection (the backend 403s share-token holders even
   * if the FE somehow tried to fetch it). When ``undefined`` we
   * default to ``false`` so an integration that hasn't been updated
   * to plumb this flag yet stays on the side of "hide private
   * surfaces".
   */
  viewerIsOwner?: boolean;
}

/**
 * The wire-level critical-moment shape carries the canonical fields
 * (``event_id``, ``kind``, ``explanation``, ``what_to_do_instead``,
 * ``severity``, ``occurred_at``). The P0-2 grader does NOT yet emit a
 * ``file_path`` / ``start_line`` / ``end_line`` triple on every moment, but
 * the surrounding supervision events (``patch.applied``, ``agent.responded``)
 * commonly carry them in their payload. We read whichever the moment exposes
 * — defensively typed since the field is optional on the wire — and fall
 * back to scanning the moment's event payload for ``files_changed[0].path``
 * / ``line`` / ``range`` style fields. Worst case the moment yields no line
 * anchor and the marker is suppressed for it (per design).
 */
interface RichCriticalMoment extends CriticalMoment {
  file_path?: string | null;
  start_line?: number | null;
  end_line?: number | null;
  label?: string | null;
}

/**
 * P0-2 — assembles the three load-bearing pieces of the report's training
 * surface:
 *
 *   1. **Critical moment** scrubber — pinned at the top so the user's
 *      attention lands on the most actionable diagnostic first.
 *   2. **Three-way diff** — user's diff vs. ideal vs. (collapsible)
 *      agent's original patch.
 *
 * P1-5: clicking the scrubber's "show in timeline" button now also drives
 * the diff panes to the moment's affected line via the
 * ``ThreeWayDiffHandle.scrollTo`` imperative method. Moments without a
 * resolvable line anchor still fire the timeline scroll — only the diff
 * scroll is suppressed.
 */
export function PostMortemWalkthrough({
  submission,
  events,
  onScrollToEvent,
  viewerIsOwner = false,
}: PostMortemWalkthroughProps) {
  // ``moments`` is a derived value — stabilising it through useMemo
  // keeps the downstream hooks' dependency arrays clean and prevents
  // every parent re-render from invalidating the load-bearing
  // projection. The lint rule that catches this would otherwise flag
  // the useMemo / useCallback below.
  const moments = React.useMemo<RichCriticalMoment[]>(
    () => (submission.critical_moments ?? []) as RichCriticalMoment[],
    [submission.critical_moments],
  );

  const diffRef = React.useRef<ThreeWayDiffHandle | null>(null);

  const loadBearingMoments = React.useMemo<LoadBearingMoment[]>(() => {
    return moments
      .map((m) => projectLoadBearing(m, events))
      .filter((m): m is LoadBearingMoment => m !== null);
  }, [moments, events]);

  const handleScrollToEvent = React.useCallback(
    (eventId: number) => {
      onScrollToEvent(eventId);
      const moment = moments.find((m) => m.event_id === eventId);
      if (!moment) return;
      const anchor = projectLoadBearing(moment, events);
      if (!anchor || !anchor.file_path || typeof anchor.start_line !== "number") {
        // Moment has no usable line anchor (e.g. "submitted without
        // verification") — timeline scroll still fired above; diff scroll
        // suppressed per design.
        return;
      }
      diffRef.current?.scrollTo(anchor.file_path, anchor.start_line);
    },
    [moments, events, onScrollToEvent],
  );

  // P1-4 — scroll the "// your notes" section into view when the
  // coaching reflection's note anchor is clicked. The Section header
  // lives in ReportView.tsx with id ``scratchpad-heading``; finding
  // it by id here keeps the integration loose (we don't need a ref
  // to a sibling component) and tolerates the heading being absent
  // on share-token views (the parent already gates this component on
  // ``viewerIsOwner``).
  // NOTE: defined above the early return so React's rule-of-hooks holds
  // — the conditional return below must not change the hook call order.
  const scrollToScratchpad = React.useCallback(() => {
    if (typeof document === "undefined") return;
    const heading = document.getElementById("scratchpad-heading");
    if (heading) {
      heading.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, []);

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
          onScrollToEvent={handleScrollToEvent}
        />
      ) : null}
      {viewerIsOwner ? (
        <CoachingReflection
          submissionId={submission.id}
          onScrollToEvent={onScrollToEvent}
          onScrollToScratchpad={scrollToScratchpad}
        />
      ) : null}
      <ThreeWayDiff
        ref={diffRef}
        userDiff={submission.final_diff ?? ""}
        idealDiff={submission.ideal_solution_diff ?? ""}
        agentPatchDiff={submission.agent_patch_diff ?? null}
        criticalMoments={loadBearingMoments}
      />
    </div>
  );
}

// ── Helpers ────────────────────────────────────────────────────────────────

/**
 * Best-effort projection of a CriticalMoment onto a LoadBearingMoment. The
 * grader may carry the file path + line range directly on the moment row;
 * when it doesn't (current persisted shape), we read the surrounding
 * supervision event's payload (``patch.applied`` payloads usually include
 * ``files_changed[].path`` + a hunk line). The label is the moment's short
 * scrubber copy.
 */
function projectLoadBearing(
  moment: RichCriticalMoment,
  events: SupervisionEvent[],
): LoadBearingMoment | null {
  const filePath = moment.file_path ?? lookupFilePath(moment, events);
  const startLine = moment.start_line ?? lookupStartLine(moment, events);
  return {
    event_id: moment.event_id,
    file_path: filePath ?? undefined,
    start_line: startLine ?? undefined,
    end_line: moment.end_line ?? undefined,
    label: moment.label ?? moment.explanation ?? "load-bearing",
  };
}

function lookupFilePath(
  moment: CriticalMoment,
  events: SupervisionEvent[],
): string | undefined {
  const ev = events.find((e) => e.id === moment.event_id);
  if (!ev) return undefined;
  const payload = ev.payload as Record<string, unknown> | undefined;
  if (!payload) return undefined;
  const direct = payload["file_path"];
  if (typeof direct === "string" && direct.length > 0) return direct;
  const file = payload["file"];
  if (typeof file === "string" && file.length > 0) return file;
  const files = payload["files_changed"];
  if (Array.isArray(files) && files.length > 0) {
    const head = files[0] as { path?: unknown };
    if (head && typeof head.path === "string") return head.path;
  }
  return undefined;
}

function lookupStartLine(
  moment: CriticalMoment,
  events: SupervisionEvent[],
): number | undefined {
  const ev = events.find((e) => e.id === moment.event_id);
  if (!ev) return undefined;
  const payload = ev.payload as Record<string, unknown> | undefined;
  if (!payload) return undefined;
  const lineRaw = payload["line"] ?? payload["start_line"];
  if (typeof lineRaw === "number" && Number.isFinite(lineRaw)) return lineRaw;
  const range = payload["range"];
  if (range && typeof range === "object") {
    const r = range as { start?: unknown };
    if (typeof r.start === "number" && Number.isFinite(r.start)) return r.start;
  }
  return undefined;
}

/** Coerce a strength/weakness wire entry into a strict EvidenceEntry. Useful
 *  in tests + downstream renderers that don't want the union complexity. */
export { asEvidenceEntry } from "@arena/shared-types";
export type { StrengthOrString };
