"use client";

/**
 * P0-3 — Private "// your attempts" strip on the mission detail page.
 *
 * Renders ONLY for signed-in callers (the backend gates ``your_attempts``
 * on ``viewer is not None``). For first-time attempts (count === 0) the
 * strip stays hidden — the StartMissionButton above is enough signal.
 *
 * The data contract is:
 *   - ``best_score`` reflects the user's best uncapped attempt (falling
 *     back to a gave-up attempt only when no uncapped attempt exists).
 *   - ``latest_score`` is the most recently graded attempt regardless of
 *     cap.
 *   - ``delta`` is the signed difference from the first attempt to the
 *     latest. Positive = improving.
 *
 * Attempt count is NEVER surfaced on the public profile — see ADR 0009.
 */

import * as React from "react";
import Link from "next/link";
import { ArrowDown, ArrowUp, Flag, Minus } from "lucide-react";
import type { YourAttempts } from "@arena/shared-types";
import { cn } from "@/lib/utils";

export interface YourAttemptsStripProps {
  attempts: YourAttempts;
}

export function YourAttemptsStrip({ attempts }: YourAttemptsStripProps) {
  if (attempts.count === 0) return null;

  const deltaSign =
    typeof attempts.delta === "number"
      ? attempts.delta > 0
        ? "up"
        : attempts.delta < 0
          ? "down"
          : "flat"
      : null;

  return (
    <section
      aria-labelledby="your-attempts-heading"
      className="mt-6 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-4 py-3"
      data-testid="your-attempts-strip"
    >
      <header className="flex items-center justify-between gap-2">
        <h2
          id="your-attempts-heading"
          className="font-mono text-[10.5px] uppercase tracking-[0.16em] text-[var(--color-muted-foreground)]"
        >
          // your attempts
        </h2>
        <p className="font-mono text-[10.5px] text-[var(--color-muted-foreground)]">
          {attempts.count}× attempted
        </p>
      </header>

      <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
        <Cell label="best">
          <span className="font-mono text-base font-semibold tabular-nums">
            {attempts.best_score ?? "—"}
          </span>
          {attempts.best_was_gave_up ? (
            <span
              title="Best attempt was a give-up (capped at 50)"
              className="ml-1 inline-flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-[var(--color-warning)]"
            >
              <Flag className="size-2.5" aria-hidden />
              gave up
            </span>
          ) : null}
          {attempts.best_submission_id ? (
            <Link
              href={`/report/${attempts.best_submission_id}`}
              className="ml-2 font-mono text-[10.5px] text-[var(--color-muted-foreground)] underline-offset-2 hover:text-[var(--color-foreground)] hover:underline"
            >
              view
            </Link>
          ) : null}
        </Cell>

        <Cell label="latest">
          <span className="font-mono text-base font-semibold tabular-nums">
            {attempts.latest_score ?? "—"}
          </span>
          {attempts.latest_submission_id &&
          attempts.latest_submission_id !== attempts.best_submission_id ? (
            <Link
              href={`/report/${attempts.latest_submission_id}`}
              className="ml-2 font-mono text-[10.5px] text-[var(--color-muted-foreground)] underline-offset-2 hover:text-[var(--color-foreground)] hover:underline"
            >
              view
            </Link>
          ) : null}
        </Cell>

        <Cell label="delta vs first">
          {typeof attempts.delta === "number" ? (
            <span
              className={cn(
                "inline-flex items-center gap-1 font-mono text-base tabular-nums",
                deltaSign === "up" && "text-[var(--color-success)]",
                deltaSign === "down" && "text-[var(--color-danger)]",
                deltaSign === "flat" && "text-[var(--color-muted-foreground)]",
              )}
            >
              {deltaSign === "up" ? (
                <ArrowUp className="size-3.5" aria-hidden />
              ) : deltaSign === "down" ? (
                <ArrowDown className="size-3.5" aria-hidden />
              ) : (
                <Minus className="size-3.5" aria-hidden />
              )}
              {attempts.delta > 0 ? `+${attempts.delta}` : attempts.delta}
            </span>
          ) : (
            <span className="font-mono text-base text-[var(--color-muted-foreground)]">
              —
            </span>
          )}
        </Cell>

        <Cell label="policy">
          <span className="font-mono text-xs text-[var(--color-muted-foreground)]">
            best per mission
          </span>
        </Cell>
      </dl>
    </section>
  );
}

function Cell({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-muted-foreground)]">
        {label}
      </dt>
      <dd className="mt-0.5 flex items-baseline gap-1.5">{children}</dd>
    </div>
  );
}
