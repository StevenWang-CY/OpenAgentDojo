"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import type { Difficulty, Mission } from "@arena/shared-types";
import { ApiError, listMissions } from "@/lib/api";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatEstimatedMinutes } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Mission roster — dense, table-like list of every published mission with
 * the ``failure_mode_id`` made first-class (the actual differentiator
 * between scenarios, previously buried under a generic short_description).
 *
 * Replaces the old horizontally-scrolling "carousel" of cards. We keep the
 * exported name ``ScenarioCarousel`` so the marketing page import path
 * stays stable, but the visual is intentionally not a carousel any more.
 *
 * Offline / preview semantics match the previous component: the static
 * fallback list fires only when the API is unreachable, never when it
 * returned an empty catalog (we render a real empty state instead).
 */
export function ScenarioCarousel() {
  const { data, isLoading, error, isError } = useQuery({
    queryKey: ["missions"],
    queryFn: ({ signal }) => listMissions(signal),
    retry: (failureCount, e) => {
      if (e instanceof ApiError && e.status === 0) return false;
      return failureCount < 1;
    },
  });

  const apiUnreachable = isError && (!data || data.length === 0);
  const missions: Mission[] =
    data && data.length > 0 ? data : apiUnreachable ? FALLBACK_MISSIONS : [];
  const reachableButEmpty =
    !isLoading && !isError && data && data.length === 0;

  return (
    <section
      aria-labelledby="scenarios-heading"
      id="missions"
      className="border-b border-[var(--color-border)]"
    >
      <div className="mx-auto max-w-6xl px-6 py-24">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
          // mission catalog
        </p>
        <h2
          id="scenarios-heading"
          className="mt-2 max-w-[700px] text-3xl font-semibold tracking-tight"
        >
          Ten scenarios. Ten distinct failure modes.
        </h2>
        <p className="mt-3 max-w-[620px] text-[var(--color-muted-foreground)]">
          Each row is a real repository with a deliberately-flawed agent
          patch and a hidden failure mode. Pick a category that looks
          uncomfortable.
        </p>

        {isLoading ? (
          <div className="mt-10 space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full rounded-md" />
            ))}
          </div>
        ) : reachableButEmpty ? (
          <div className="mt-10 rounded-[10px] border border-dashed border-[var(--color-border)] bg-[var(--color-surface)] px-6 py-10 text-center text-sm text-[var(--color-muted-foreground)]">
            The catalog is empty. Missions will appear here as soon as content
            ships.
          </div>
        ) : (
          <div className="mt-10 overflow-hidden rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)]">
            <RosterHeader />
            <ul role="list" className="divide-y divide-[var(--color-border)]">
              {missions.map((m, i) => (
                <li key={m.id}>
                  <MissionRow
                    mission={m}
                    index={i}
                    preview={apiUnreachable}
                  />
                </li>
              ))}
            </ul>
          </div>
        )}

        {apiUnreachable ? (
          <p className="mt-2 text-[11px] text-[var(--color-muted-foreground)]">
            Showing a preview catalog — connect the backend to see live mission
            metadata.
          </p>
        ) : null}
      </div>
    </section>
  );
}

/* ── Row chrome ───────────────────────────────────────────────────────── */

function RosterHeader() {
  return (
    <div className="grid grid-cols-[56px_1fr_56px_40px] items-center gap-4 border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-5 py-2.5 font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--color-muted-foreground)] sm:grid-cols-[56px_1.6fr_1fr_80px_80px_40px]">
      <span>id</span>
      <span>mission</span>
      <span className="hidden sm:inline">category</span>
      <span className="text-right">level</span>
      <span className="hidden text-right sm:inline">est</span>
      <span />
    </div>
  );
}

function MissionRow({
  mission,
  index,
  preview,
}: {
  mission: Mission;
  index: number;
  preview: boolean;
}) {
  const baseClasses =
    "group grid grid-cols-[56px_1fr_56px_40px] items-center gap-4 px-5 py-4 transition-colors duration-150 sm:grid-cols-[56px_1.6fr_1fr_80px_80px_40px] " +
    (preview ? "cursor-default" : "hover:bg-[var(--color-muted)]");
  const indexLabel = String(index + 1).padStart(2, "0");
  const failureMode = mission.failure_mode_id;

  const body = (
    <>
      <span className="font-mono text-xs text-[var(--color-muted-foreground)]">
        {indexLabel}
      </span>
      <div className="min-w-0">
        <div className="truncate text-sm font-medium tracking-tight">
          {mission.title}
        </div>
        {failureMode ? (
          <div className="mt-0.5 font-mono text-[11px] text-[var(--color-muted-foreground)]">
            failure_mode ·{" "}
            <b className="font-medium text-[var(--color-warning)]">
              {failureMode}
            </b>
          </div>
        ) : null}
      </div>
      <div className="hidden font-mono text-[11px] text-[var(--color-muted-foreground)] sm:block">
        {mission.category}
      </div>
      <div
        className={cn(
          "text-right font-mono text-[11px] uppercase tracking-[0.04em]",
          DIFFICULTY_COLOR[mission.difficulty]
        )}
      >
        {mission.difficulty}
      </div>
      <div className="hidden text-right font-mono text-xs text-[var(--color-muted-foreground)] sm:block">
        {formatEstimatedMinutes(mission.estimated_minutes)}
      </div>
      <div
        aria-hidden
        className="text-right font-mono text-[var(--color-muted-foreground)] transition-[transform,color] duration-150 group-hover:translate-x-0.5 group-hover:text-[var(--color-foreground)]"
      >
        →
      </div>
    </>
  );

  if (preview) {
    return (
      <div
        aria-disabled
        className={baseClasses}
        title="Preview only — backend offline"
      >
        {body}
      </div>
    );
  }
  return (
    <Link href={`/missions/${mission.id}`} className={baseClasses}>
      {body}
    </Link>
  );
}

const DIFFICULTY_COLOR: Record<Difficulty, string> = {
  beginner: "text-[var(--color-success)]",
  intermediate: "text-[var(--color-warning)]",
  advanced: "text-[var(--color-danger)]",
};

/** Frozen preview for offline / pre-seed environments. Mirrors §14 missions. */
const FALLBACK_MISSIONS: Mission[] = [
  {
    id: "auth-cookie-expiration",
    title: "Expired Session Cookie Still Grants Access",
    short_description:
      "Users with expired session cookies can still access protected routes.",
    difficulty: "intermediate",
    category: "auth",
    estimated_minutes: 35,
    skills_tested: ["auth", "security", "test-writing"],
    failure_mode_id: "checks_presence_not_expiration",
    version: 1,
    published: true,
  },
  {
    id: "agent-wrong-file",
    title: "Agent Edits the Wrong File",
    short_description:
      "Profile names render truncated. The agent fixes the symptom in CSS instead of the backend serializer.",
    difficulty: "beginner",
    category: "frontend",
    estimated_minutes: 20,
    skills_tested: ["frontend", "review"],
    failure_mode_id: "wrong_layer_fix",
    version: 1,
    published: true,
  },
  {
    id: "missing-regression-test",
    title: "Missing Regression Test (Duplicate Submission)",
    short_description:
      "Form double-submit creates duplicate rows. The agent's in-memory idempotency check forgets server restarts.",
    difficulty: "intermediate",
    category: "testing",
    estimated_minutes: 30,
    skills_tested: ["test-writing", "database"],
    failure_mode_id: "in_memory_idempotency",
    version: 1,
    published: true,
  },
  {
    id: "overfitted-test-fix",
    title: "Overfitted Test Fix (Price Calculation)",
    short_description:
      "Agent hardcodes a return value to make one visible test pass while quietly breaking everything else.",
    difficulty: "beginner",
    category: "testing",
    estimated_minutes: 20,
    skills_tested: ["test-writing", "review"],
    failure_mode_id: "overfitted_visible_test",
    version: 1,
    published: true,
  },
  {
    id: "security-validation-removed",
    title: "Security Validation Removed (Settings Update)",
    short_description:
      "The agent removes an authorization check to silence a 403 the user is supposed to see.",
    difficulty: "advanced",
    category: "security",
    estimated_minutes: 35,
    skills_tested: ["security", "review"],
    failure_mode_id: "removes_authorization",
    version: 1,
    published: true,
  },
  {
    id: "excessive-rewrite",
    title: "Excessive Rewrite (Dashboard Loading)",
    short_description:
      "One missing setLoading(false) becomes a full state-machine refactor with new hooks and abstractions.",
    difficulty: "intermediate",
    category: "refactoring",
    estimated_minutes: 25,
    skills_tested: ["review", "frontend"],
    failure_mode_id: "scope_creep",
    version: 1,
    published: true,
  },
  {
    id: "dependency-misuse",
    title: "Dependency Misuse (Date Formatting)",
    short_description:
      "Agent reaches for a deprecated package and ignores DST when formatting report timestamps.",
    difficulty: "intermediate",
    category: "api",
    estimated_minutes: 30,
    skills_tested: ["api", "agent-safety"],
    failure_mode_id: "deprecated_dep",
    version: 1,
    published: true,
  },
  {
    id: "async-race-condition",
    title: "Async Race Condition (Queue Processing)",
    short_description:
      "The agent's 'fix' for double-processing leaves a check-then-set window wide open under concurrency.",
    difficulty: "advanced",
    category: "database",
    estimated_minutes: 40,
    skills_tested: ["debugging", "database"],
    failure_mode_id: "non_atomic_check_then_set",
    version: 1,
    published: true,
  },
  {
    id: "api-contract-drift",
    title: "API Contract Drift",
    short_description:
      "Backend renamed a field. The agent fixes one consumer and misses two more, crashing the dashboard.",
    difficulty: "intermediate",
    category: "api",
    estimated_minutes: 30,
    skills_tested: ["api", "review"],
    failure_mode_id: "partial_consumer_update",
    version: 1,
    published: true,
  },
  {
    id: "typecheck-ignored",
    title: "Typecheck Ignored (Avatar Upload)",
    short_description:
      "Agent silences the type-checker with three 'as any' casts instead of carrying the new optional field through.",
    difficulty: "beginner",
    category: "review",
    estimated_minutes: 20,
    skills_tested: ["review", "agent-safety"],
    failure_mode_id: "as_any_to_silence_ts",
    version: 1,
    published: true,
  },
];
