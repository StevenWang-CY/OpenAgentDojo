"use client";

import { Award, CheckCircle2, Trophy, XCircle } from "lucide-react";
import type { ScoreBreakdown } from "@arena/shared-types";
import { ScoreRadar } from "@/components/report/ScoreRadar";
import { DimensionBreakdown } from "@/components/report/DimensionBreakdown";

/**
 * Marketing-only "screenshot" of what a graded report looks like, rendered
 * from real `ScoreRadar` + `DimensionBreakdown` components against a frozen
 * seed so the visual matches the actual product (no fake screenshots).
 */
export function SampleReport() {
  return (
    <section
      aria-labelledby="sample-report-heading"
      className="mx-auto max-w-6xl px-6 py-20"
    >
      <header className="max-w-2xl">
        <p className="text-xs uppercase tracking-[0.2em] text-[var(--color-muted-foreground)]">
          What you take home
        </p>
        <h2
          id="sample-report-heading"
          className="mt-2 text-3xl font-semibold tracking-tight"
        >
          A score report on the process, not the patch.
        </h2>
        <p className="mt-3 text-[var(--color-muted-foreground)]">
          Hidden tests and structural validators feed seven rubric dimensions.
          Strengths and weaknesses are explained in plain language, with
          replayable timeline evidence.
        </p>
      </header>

      <div className="mt-10 overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-elevated">
        {/* Faux browser chrome — sets the "screenshot" expectation. */}
        <div className="flex items-center gap-1.5 border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3 py-2">
          <span
            aria-hidden
            className="size-2.5 rounded-full bg-[oklch(from_var(--color-danger)_l_c_h/0.8)]"
          />
          <span
            aria-hidden
            className="size-2.5 rounded-full bg-[oklch(from_var(--color-warning)_l_c_h/0.8)]"
          />
          <span
            aria-hidden
            className="size-2.5 rounded-full bg-[oklch(from_var(--color-success)_l_c_h/0.8)]"
          />
          <p className="ml-3 truncate font-mono text-[11px] text-[var(--color-muted-foreground)]">
            arena.dev/report/01-auth-cookie-expiration
          </p>
        </div>

        <div className="grid gap-6 p-6 lg:grid-cols-[1.1fr,1fr]">
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Trophy
                className="size-5 text-[var(--color-primary)]"
                aria-hidden
              />
              <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-muted-foreground)]">
                Mission 01 · Auth Cookie Expiration
              </p>
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-5xl font-bold tracking-tight text-[var(--color-primary)]">
                {SAMPLE_REPORT.total}
              </span>
              <span className="text-xl text-[var(--color-muted-foreground)]">
                / 100
              </span>
            </div>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              Failure mode identified · 3 of 4 hidden tests passing.
            </p>

            <ul className="space-y-1.5 text-sm">
              {SAMPLE_REPORT.strengths.map((s) => (
                <li key={s} className="flex items-start gap-2">
                  <CheckCircle2
                    className="mt-0.5 size-4 shrink-0 text-[var(--color-success)]"
                    aria-hidden
                  />
                  {s}
                </li>
              ))}
              {SAMPLE_REPORT.weaknesses.map((w) => (
                <li key={w} className="flex items-start gap-2">
                  <XCircle
                    className="mt-0.5 size-4 shrink-0 text-[var(--color-danger)]"
                    aria-hidden
                  />
                  {w}
                </li>
              ))}
            </ul>

            <div className="flex flex-wrap gap-2 pt-2">
              {SAMPLE_REPORT.badges.map((b) => (
                <span
                  key={b}
                  className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-[oklch(from_var(--color-accent)_l_c_h/0.15)] px-2.5 py-1 text-[11px] font-medium"
                >
                  <Award
                    className="size-3 text-[var(--color-accent)]"
                    aria-hidden
                  />
                  {b}
                </span>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-4">
            <ScoreRadar dimensions={SAMPLE_REPORT.dimensions} />
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-3">
              <DimensionBreakdown dimensions={SAMPLE_REPORT.dimensions} />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

const SAMPLE_REPORT: {
  total: number;
  dimensions: ScoreBreakdown;
  strengths: string[];
  weaknesses: string[];
  badges: string[];
} = {
  total: 78,
  dimensions: {
    final_correctness: {
      score: 24,
      max: 30,
      signals: ["3/4 hidden tests passed", "visible suite green"],
    },
    verification: {
      score: 14,
      max: 20,
      signals: ["ran auth-focused tests", "did not run typecheck"],
    },
    agent_review: {
      score: 11,
      max: 15,
      signals: ["opened diff", "1 corrective prompt"],
    },
    prompt_quality: {
      score: 7,
      max: 10,
      signals: ["mentioned regression test", "scoped to auth files"],
    },
    context_selection: {
      score: 8,
      max: 10,
      signals: ["selected middleware + session.ts"],
    },
    safety: {
      score: 9,
      max: 10,
      signals: ["no validation guards removed"],
    },
    diff_minimality: {
      score: 5,
      max: 5,
      signals: ["12 lines added vs. p50 of 18"],
    },
  },
  strengths: [
    "Selected the right context up front",
    "Asked for a regression test in the very first prompt",
  ],
  weaknesses: [
    "Did not run typecheck before submitting",
    "Missed the refresh-token edge case",
  ],
  badges: ["regression-test-writer", "agent-skeptic"],
};
