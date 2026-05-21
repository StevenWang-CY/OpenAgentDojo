"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ChevronLeft, ChevronRight, Clock } from "lucide-react";
import type { Mission } from "@arena/shared-types";
import { ApiError, listMissions } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { DifficultyBadge } from "@/components/catalog/DifficultyBadge";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatEstimatedMinutes } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Horizontally-scrolling marketing strip of every published mission. Used on
 * the landing page so visitors can scan all 10 scenarios at a glance. Falls
 * back to a frozen sample list when the API is offline so the marketing page
 * never looks broken locally.
 */
export function ScenarioCarousel() {
  const scrollerRef = React.useRef<HTMLUListElement | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["missions"],
    queryFn: ({ signal }) => listMissions(signal),
    retry: (failureCount, e) => {
      if (e instanceof ApiError && e.status === 0) return false;
      return failureCount < 1;
    },
  });

  const missions: Mission[] = data && data.length > 0 ? data : FALLBACK_MISSIONS;
  const offline = !isLoading && (error || (data && data.length === 0));

  function scrollBy(direction: 1 | -1) {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollBy({ left: direction * Math.max(320, el.clientWidth * 0.75), behavior: "smooth" });
  }

  return (
    <section
      aria-labelledby="scenarios-heading"
      className="mx-auto max-w-6xl px-6 py-20"
    >
      <header className="flex items-end justify-between gap-4">
        <div className="max-w-2xl">
          <p className="text-xs uppercase tracking-[0.2em] text-[var(--color-muted-foreground)]">
            Mission catalog
          </p>
          <h2
            id="scenarios-heading"
            className="mt-2 text-3xl font-semibold tracking-tight"
          >
            Ten scenarios. Ten distinct failure modes.
          </h2>
          <p className="mt-3 text-[var(--color-muted-foreground)]">
            Each mission is a real repository with a deliberately-flawed agent
            patch. Pick a category that looks uncomfortable.
          </p>
        </div>
        <div className="hidden items-center gap-1 sm:flex">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => scrollBy(-1)}
            aria-label="Scroll carousel left"
          >
            <ChevronLeft className="size-4" aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => scrollBy(1)}
            aria-label="Scroll carousel right"
          >
            <ChevronRight className="size-4" aria-hidden />
          </Button>
        </div>
      </header>

      {isLoading ? (
        <div className="mt-8 flex gap-4 overflow-hidden">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-44 w-72 shrink-0 rounded-xl" />
          ))}
        </div>
      ) : (
        <ul
          ref={scrollerRef}
          role="list"
          aria-label="Mission carousel"
          className="mt-8 flex snap-x snap-mandatory gap-4 overflow-x-auto pb-4 [scrollbar-width:thin]"
        >
          {missions.map((mission) => (
            <li
              key={mission.id}
              className="w-72 shrink-0 snap-start"
            >
              <ScenarioCard mission={mission} />
            </li>
          ))}
        </ul>
      )}

      {offline ? (
        <p className="mt-2 text-[11px] text-[var(--color-muted-foreground)]">
          Showing a preview catalog — connect the backend to see live mission
          metadata.
        </p>
      ) : null}
    </section>
  );
}

function ScenarioCard({ mission }: { mission: Mission }) {
  const href = `/missions/${mission.id}` as const;
  return (
    <Link
      href={href}
      className={cn(
        "group flex h-full flex-col gap-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 shadow-soft",
        "transition-all duration-180 ease-macos hover:-translate-y-0.5 hover:shadow-elevated"
      )}
    >
      <div className="flex items-center justify-between">
        <Badge tone="outline" className="font-mono text-[10px] tracking-normal">
          {mission.category}
        </Badge>
        <DifficultyBadge difficulty={mission.difficulty} />
      </div>
      <p className="line-clamp-2 text-sm font-semibold tracking-tight">
        {mission.title}
      </p>
      <p className="line-clamp-2 text-xs text-[var(--color-muted-foreground)]">
        {mission.short_description}
      </p>
      <div className="mt-auto flex items-center justify-between border-t border-[var(--color-border)] pt-3 text-[11px] text-[var(--color-muted-foreground)]">
        <span className="inline-flex items-center gap-1">
          <Clock className="size-3" aria-hidden />
          {formatEstimatedMinutes(mission.estimated_minutes)}
        </span>
        <span className="inline-flex items-center gap-1 text-[var(--color-primary)] opacity-0 transition-opacity duration-150 group-hover:opacity-100">
          Start <ArrowRight className="size-3" aria-hidden />
        </span>
      </div>
    </Link>
  );
}

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
