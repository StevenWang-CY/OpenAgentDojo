"use client";

import type { ScoreBreakdown } from "@arena/shared-types";
import { ScoreRadar } from "@/components/report/ScoreRadar";

/**
 * Marketing-only "screenshot" of what a graded report looks like, rendered
 * from real components against a frozen seed so the visual matches the
 * actual product (no fake screenshots). The chrome is intentionally
 * unadorned — terminal-flavored mono glyphs (✓ / ✕) and small mono badges,
 * not the lucide Trophy/Award decorations the previous design used.
 */
export function SampleReport() {
  return (
    <section
      aria-labelledby="sample-report-heading"
      className="border-b border-[var(--color-border)]"
    >
      <div className="mx-auto max-w-6xl px-6 py-24">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
          {"// what you take home"}
        </p>
        <h2
          id="sample-report-heading"
          className="mt-2 max-w-[700px] text-3xl font-semibold tracking-tight"
        >
          A score report on the process, not the patch.
        </h2>
        <p className="mt-3 max-w-[620px] text-[var(--color-muted-foreground)]">
          Hidden tests and structural validators feed seven rubric dimensions.
          Strengths and weaknesses are explained in plain language, with
          replayable timeline evidence.
        </p>

        <div className="mt-10 overflow-hidden rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] shadow-soft">
          {/* Faux browser chrome — sets the "screenshot" expectation. */}
          <div className="flex items-center gap-1.5 border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3.5 py-2.5">
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
            <p className="ml-2.5 truncate font-mono text-[11px] text-[var(--color-muted-foreground)]">
              openagentdojo.app/report/sample
            </p>
            <span className="ml-2 rounded border border-[var(--color-border)] bg-[var(--color-muted)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[var(--color-muted-foreground)]">
              sample
            </span>
          </div>

          <div className="grid gap-8 p-7 md:p-8 lg:grid-cols-[1.05fr_1fr]">
            <div>
              <p className="font-mono text-[11px] uppercase tracking-[0.06em] text-[var(--color-muted-foreground)]">
                mission 01 · auth cookie expiration
              </p>
              <div className="mt-2.5 flex items-baseline gap-1.5 font-mono">
                <span className="text-[64px] font-semibold leading-none tracking-[-0.04em] tabular-nums text-[var(--color-foreground)]">
                  {SAMPLE_REPORT.total}
                </span>
                <span className="text-[22px] text-[var(--color-muted-foreground)]">
                  / 100
                </span>
              </div>
              <p className="mt-1.5 text-[13px] text-[var(--color-muted-foreground)]">
                Failure mode identified · 3 of 4 hidden tests passing.
              </p>

              <ul className="mt-5 grid gap-1.5 text-[13px]">
                {SAMPLE_REPORT.strengths.map((s) => (
                  <li
                    key={s}
                    className="grid grid-cols-[16px_1fr] items-start gap-2"
                  >
                    <span
                      aria-hidden
                      className="font-mono font-semibold text-[var(--color-success)]"
                    >
                      ✓
                    </span>
                    <span>{s}</span>
                  </li>
                ))}
                {SAMPLE_REPORT.weaknesses.map((w) => (
                  <li
                    key={w}
                    className="grid grid-cols-[16px_1fr] items-start gap-2"
                  >
                    <span
                      aria-hidden
                      className="font-mono font-semibold text-[var(--color-danger)]"
                    >
                      ✕
                    </span>
                    <span>{w}</span>
                  </li>
                ))}
              </ul>

              <div className="mt-5 flex flex-wrap gap-1.5">
                {SAMPLE_REPORT.badges.map((b) => (
                  <span
                    key={b}
                    className="rounded border border-[var(--color-border)] px-2 py-0.5 font-mono text-[11px]"
                  >
                    <b className="font-medium text-[var(--color-primary)]">+</b>{" "}
                    {b}
                  </span>
                ))}
              </div>
            </div>

            <div className="flex flex-col gap-4">
              <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4">
                <ScoreRadar dimensions={SAMPLE_REPORT.dimensions} />
              </div>
              <div className="grid gap-3">
                {(
                  Object.entries(SAMPLE_REPORT.dimensions) as Array<
                    [keyof ScoreBreakdown, ScoreBreakdown[keyof ScoreBreakdown]]
                  >
                ).map(([k, d]) => {
                  const score = d.score ?? 0;
                  const pct = d.max > 0 ? Math.round((score / d.max) * 100) : 0;
                  return (
                    <div
                      key={k}
                      className="grid grid-cols-[1fr_auto] items-baseline gap-2"
                    >
                      <div className="text-[13px]">{DIM_LABEL[k]}</div>
                      <div className="font-mono text-xs text-[var(--color-muted-foreground)]">
                        <b className="font-semibold text-[var(--color-foreground)]">
                          {d.score ?? "—"}
                        </b>{" "}
                        / {d.max}
                      </div>
                      <div className="col-span-2 mt-1 h-1 overflow-hidden rounded-sm bg-[var(--color-muted)]">
                        <div
                          className="h-full bg-[var(--color-foreground)]"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

const DIM_LABEL: Record<keyof ScoreBreakdown, string> = {
  final_correctness: "Final patch correctness",
  verification: "Verification discipline",
  agent_review: "Agent output review",
  prompt_quality: "Prompt quality",
  context_selection: "Context selection",
  safety: "Safety awareness",
  diff_minimality: "Diff minimality",
};

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
      score: 11,
      max: 15,
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
      score: 8,
      max: 10,
      signals: ["12 lines added vs. p50 of 18"],
    },
  },
  strengths: [
    "Selected the right context up front (middleware + session.ts)",
    "Asked for a regression test in the very first prompt",
  ],
  weaknesses: [
    "Did not run typecheck before submitting",
    "Missed the refresh-token edge case",
  ],
  badges: ["regression-test-writer", "agent-skeptic"],
};
