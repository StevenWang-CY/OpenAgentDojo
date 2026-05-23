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
 *   - ``score_history`` is the most recent 12 totals in chronological
 *     order, powering the inline sparkline on the delta cell.
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
          {"// your attempts"}
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
            <DeltaWithSparkline
              delta={attempts.delta}
              sign={deltaSign}
              history={attempts.score_history ?? []}
            />
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

/**
 * Delta cell with a hover-revealed sparkline (P0_DESIGN spec).
 *
 * The trigger is the same arrow + signed number shown by the original
 * strip; on hover (or keyboard focus) we surface a small line-chart of
 * the score history above the cell. The sparkline is pure inline SVG
 * to keep the bundle slim — no chart library dependency for a 60×16 px
 * decoration that ships on every signed-in mission detail page.
 *
 * When ``history`` has fewer than two scores the sparkline is omitted
 * (a single point isn't a trend) and the cell falls back to the bare
 * arrow + number.
 */
function DeltaWithSparkline({
  delta,
  sign,
  history,
}: {
  delta: number;
  sign: "up" | "down" | "flat" | null;
  history: number[];
}) {
  const [open, setOpen] = React.useState(false);
  const renderable = history.length >= 2;
  const toneClass =
    sign === "up"
      ? "text-[var(--color-success)]"
      : sign === "down"
        ? "text-[var(--color-danger)]"
        : "text-[var(--color-muted-foreground)]";

  return (
    <span className="relative inline-flex items-center">
      <button
        type="button"
        aria-label={
          renderable
            ? `Delta from first attempt: ${delta > 0 ? "+" : ""}${delta}. Show score history.`
            : `Delta from first attempt: ${delta > 0 ? "+" : ""}${delta}`
        }
        aria-expanded={renderable ? open : undefined}
        onMouseEnter={() => renderable && setOpen(true)}
        onMouseLeave={() => renderable && setOpen(false)}
        onFocus={() => renderable && setOpen(true)}
        onBlur={() => renderable && setOpen(false)}
        onClick={() => renderable && setOpen((p) => !p)}
        disabled={!renderable}
        data-testid="delta-trigger"
        className={cn(
          "inline-flex items-center gap-1 font-mono text-base tabular-nums",
          toneClass,
          renderable
            ? "cursor-help underline decoration-dotted underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-background)] rounded-sm"
            : "cursor-default",
        )}
      >
        {sign === "up" ? (
          <ArrowUp className="size-3.5" aria-hidden />
        ) : sign === "down" ? (
          <ArrowDown className="size-3.5" aria-hidden />
        ) : (
          <Minus className="size-3.5" aria-hidden />
        )}
        {delta > 0 ? `+${delta}` : delta}
      </button>

      {renderable && open ? (
        <span
          role="tooltip"
          data-testid="delta-sparkline"
          className="absolute left-1/2 top-full z-30 mt-2 -translate-x-1/2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1.5 shadow-elevated"
        >
          <Sparkline values={history} className={toneClass} />
          <span className="ml-1.5 font-mono text-[10px] text-[var(--color-muted-foreground)]">
            {history.length} attempt{history.length === 1 ? "" : "s"}
          </span>
        </span>
      ) : null}
    </span>
  );
}

/**
 * Pure-SVG line chart sized for a hover tooltip. Maps each score in
 * ``values`` to an x position by index and a y position by ``score / 100``
 * (the rubric's effective max). The line uses ``currentColor`` so the
 * caller can colour-code by trend direction via the wrapping element's
 * text colour.
 */
function Sparkline({
  values,
  className,
}: {
  values: number[];
  className?: string;
}) {
  if (values.length < 2) return null;
  const width = 60;
  const height = 16;
  const max = 100; // rubric effective_max — score is 0..100 by contract
  const step = width / (values.length - 1);
  const points = values
    .map((v, i) => {
      const x = i * step;
      const clamped = Math.max(0, Math.min(max, v));
      const y = height - (clamped / max) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden
      className={cn("inline-block align-middle", className)}
    >
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.25}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {values.map((v, i) => {
        const x = i * step;
        const clamped = Math.max(0, Math.min(max, v));
        const y = height - (clamped / max) * height;
        return (
          <circle
            key={i}
            cx={x}
            cy={y}
            r={1.5}
            fill="currentColor"
          />
        );
      })}
    </svg>
  );
}
