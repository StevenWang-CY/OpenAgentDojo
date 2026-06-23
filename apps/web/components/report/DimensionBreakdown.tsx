import type { ScoreBreakdown } from "@arena/shared-types";

interface DimensionBreakdownProps {
  dimensions: ScoreBreakdown;
}

const LABELS: Record<keyof ScoreBreakdown, string> = {
  final_correctness: "Final patch correctness",
  verification: "Verification discipline",
  agent_review: "Agent output review",
  prompt_quality: "Prompt quality",
  context_selection: "Context selection",
  safety: "Safety awareness",
  diff_minimality: "Diff minimality",
};

export function DimensionBreakdown({ dimensions }: DimensionBreakdownProps) {
  return (
    <ul className="grid gap-3.5">
      {(Object.keys(LABELS) as (keyof ScoreBreakdown)[]).map((key) => {
        const d = dimensions[key];
        const pending = d.score == null;
        const pct = !pending && d.max > 0 ? (d.score! / d.max) * 100 : 0;
        return (
          <li key={key}>
            <div className="flex items-baseline justify-between gap-3">
              <p className="text-[13px] font-medium leading-tight">
                {LABELS[key]}
                {pending ? (
                  <span className="ml-2 font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-muted-foreground)]">
                    pending
                  </span>
                ) : null}
              </p>
              <p className="font-mono text-xs text-[var(--color-muted-foreground)]">
                <b className="font-semibold text-[var(--color-foreground)]">
                  {pending ? "—" : d.score}
                </b>{" "}
                / {d.max}
              </p>
            </div>
            <div
              className={
                "mt-1 h-[3px] overflow-hidden rounded-[1px] " +
                (pending
                  ? "bg-[var(--color-muted)] [background:repeating-linear-gradient(45deg,var(--color-muted),var(--color-muted)_4px,var(--color-border-strong)_4px,var(--color-border-strong)_8px)]"
                  : "bg-[var(--color-muted)]")
              }
            >
              {!pending ? (
                <div
                  className="h-full bg-[var(--color-foreground)]"
                  style={{ width: `${pct}%` }}
                />
              ) : null}
            </div>
            {(d.signals?.length ?? 0) > 0 ? (
              <p className="mt-1 truncate font-mono text-[11px] text-[var(--color-muted-foreground)]">
                {d.signals!.join(" · ")}
              </p>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
