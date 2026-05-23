"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, Loader2 } from "lucide-react";
import type { FailureModeMastery } from "@arena/shared-types";
import { ApiError, getMySkills } from "@/lib/api";
import { Button } from "@/components/ui/Button";

export function SkillsView() {
  const skillsQuery = useQuery({
    queryKey: ["skills", "me"],
    queryFn: ({ signal }) => getMySkills(signal),
    retry: (n, err) => (err instanceof ApiError && err.status === 401 ? false : n < 1),
  });

  if (skillsQuery.isLoading) {
    return (
      <div className="mx-auto flex max-w-5xl items-center gap-2 px-6 py-16 text-sm text-[var(--color-muted-foreground)]">
        <Loader2 className="size-4 animate-spin" aria-hidden />
        Loading your skills…
      </div>
    );
  }

  if (skillsQuery.error) {
    const err =
      skillsQuery.error instanceof ApiError ? skillsQuery.error : null;
    if (err?.status === 401 || err?.status === 403) {
      return (
        <div className="mx-auto max-w-2xl px-6 py-16 text-center">
          <AlertCircle
            className="mx-auto size-6 text-[var(--color-muted-foreground)]"
            aria-hidden
          />
          <h1 className="mt-3 text-2xl font-semibold tracking-tight">
            Sign in to see your skills
          </h1>
          <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
            The skills catalog shows your per-failure-mode mastery — sign in
            to unlock it.
          </p>
          <Button asChild variant="secondary" className="mt-6">
            <Link href="/auth/sign-in?next=/skills">Sign in</Link>
          </Button>
        </div>
      );
    }
    return (
      <div className="mx-auto max-w-2xl px-6 py-16 text-center">
        <AlertCircle
          className="mx-auto size-6 text-[var(--color-danger)]"
          aria-hidden
        />
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">
          Couldn't load your skills
        </h1>
        <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
          {err?.message ?? "Unexpected error."}
        </p>
        <Button
          variant="secondary"
          className="mt-6"
          onClick={() => void skillsQuery.refetch()}
        >
          Try again
        </Button>
      </div>
    );
  }

  const catalog = skillsQuery.data;
  if (!catalog) return null;

  return (
    <main className="mx-auto max-w-5xl px-6 py-14">
      <header>
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
          <span className="text-[var(--color-primary)]">{"//"}</span> skills
          · failure-mode mastery
        </p>
        <h1 className="mt-1.5 text-3xl font-semibold tracking-tight">
          Your supervision skills
        </h1>
        <p className="mt-2.5 max-w-2xl text-[var(--color-muted-foreground)]">
          Each row is a supervision failure mode the platform tests. Pass
          ratio = how often you correctly caught the agent's mistake before
          submitting.
        </p>
        <p className="mt-1 font-mono text-xs text-[var(--color-muted-foreground)] tabular-nums">
          {catalog.total_failure_modes} failure mode
          {catalog.total_failure_modes === 1 ? "" : "s"} · {catalog.total_missions}{" "}
          mission{catalog.total_missions === 1 ? "" : "s"}
        </p>
      </header>

      <ul
        className="mt-8 grid gap-3"
        data-testid="failure-mode-catalog"
      >
        {catalog.failure_modes.map((fm) => (
          <FailureModeRow key={fm.failure_mode} row={fm} />
        ))}
      </ul>
    </main>
  );
}

function FailureModeRow({ row }: { row: FailureModeMastery }) {
  const ratio =
    row.sessions_attempted > 0
      ? row.sessions_passed / row.sessions_attempted
      : 0;
  const masteryClass = masteryColor(row.sessions_attempted, ratio);
  const pct =
    row.sessions_attempted > 0 ? Math.round(ratio * 100) : null;

  const firstMissionId = row.mission_ids[0] ?? null;

  return (
    <li
      className={`grid grid-cols-[12px_minmax(0,1fr)_auto_auto] items-center gap-4 rounded border border-[var(--color-border)] bg-[var(--color-surface)] p-4 ${
        row.sessions_attempted === 0 ? "opacity-70" : ""
      }`}
    >
      <span
        className={`inline-block size-2.5 rounded-full ${masteryClass}`}
        aria-hidden
      />
      <div className="min-w-0">
        <p className="text-sm font-semibold">
          {row.failure_mode_title ?? row.failure_mode}
        </p>
        {row.mission_ids.length > 0 ? (
          <ul className="mt-1 flex flex-wrap gap-1">
            {row.mission_ids.map((mid) => (
              <li key={mid}>
                <Link
                  href={`/missions/${mid}`}
                  className="inline-flex items-center rounded border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-1.5 py-0.5 font-mono text-[10.5px] text-[var(--color-muted-foreground)] transition-colors hover:border-[var(--color-foreground)] hover:text-[var(--color-foreground)]"
                >
                  {mid}
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className="text-right">
        <p className="font-mono text-xs tabular-nums">
          <b className="font-semibold text-[var(--color-foreground)]">
            {row.sessions_passed}
          </b>
          <span className="text-[var(--color-muted-foreground)]">
            /{row.sessions_attempted} passed
          </span>
          {pct != null ? (
            <>
              {" · "}
              <span>{pct}%</span>
            </>
          ) : null}
        </p>
        <p className="mt-0.5 font-mono text-[11px] text-[var(--color-muted-foreground)] tabular-nums">
          {row.best_score != null ? `best ${row.best_score}` : "no attempts"}
          {row.avg_score != null ? ` · avg ${row.avg_score}` : ""}
        </p>
      </div>
      {firstMissionId ? (
        <Button asChild size="sm" variant="secondary">
          <Link href={`/missions/${firstMissionId}`}>
            Start mission →
          </Link>
        </Button>
      ) : (
        <span aria-hidden />
      )}
    </li>
  );
}

function masteryColor(attempted: number, ratio: number): string {
  if (attempted === 0) return "bg-[var(--color-muted)]";
  if (ratio >= 0.8) return "bg-[var(--color-success)]";
  if (ratio >= 0.5) return "bg-[var(--color-primary)]";
  return "bg-[var(--color-danger)]";
}
