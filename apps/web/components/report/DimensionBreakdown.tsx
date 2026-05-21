import type { ScoreBreakdown } from "@arena/shared-types";
import { Progress } from "@/components/ui/Progress";

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
    <ul className="space-y-4">
      {(Object.keys(LABELS) as (keyof ScoreBreakdown)[]).map((key) => {
        const d = dimensions[key];
        return (
          <li key={key} className="space-y-1.5">
            <div className="flex items-baseline justify-between gap-3">
              <p className="text-sm font-medium">{LABELS[key]}</p>
              <p className="font-mono text-xs text-[var(--color-muted-foreground)]">
                {d.score} / {d.max}
              </p>
            </div>
            <Progress value={d.score} max={d.max} />
            {d.signals.length > 0 ? (
              <ul className="mt-1 text-xs text-[var(--color-muted-foreground)]">
                {d.signals.map((signal, idx) => (
                  <li key={idx}>• {signal}</li>
                ))}
              </ul>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
