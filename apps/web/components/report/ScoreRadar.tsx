"use client";

import * as React from "react";
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import type { ScoreBreakdown, ScoreDimension } from "@arena/shared-types";

interface ScoreRadarProps {
  dimensions: ScoreBreakdown;
  className?: string;
}

const LABELS: Record<keyof ScoreBreakdown, string> = {
  final_correctness: "Correctness",
  verification: "Verification",
  agent_review: "Agent review",
  prompt_quality: "Prompt",
  context_selection: "Context",
  safety: "Safety",
  diff_minimality: "Minimality",
};

export function ScoreRadar({ dimensions, className }: ScoreRadarProps) {
  // Wrap recharts in a boundary because the chart code touches the DOM
  // measurement APIs and has historically crashed on certain browser-quirk
  // edge cases (e.g. zero-sized containers in older Safari). Falling back
  // to a textual table keeps the dimensions readable + accessible.
  return (
    <RadarErrorBoundary fallback={<ScoreRadarFallback dimensions={dimensions} />}>
      <ScoreRadarChart dimensions={dimensions} className={className} />
    </RadarErrorBoundary>
  );
}

function ScoreRadarChart({ dimensions, className }: ScoreRadarProps) {
  const data = (Object.keys(LABELS) as (keyof ScoreBreakdown)[]).map((key) => {
    const dim: ScoreDimension = dimensions[key];
    return {
      dimension: LABELS[key],
      value: (dim.score / dim.max) * 100,
      raw: dim.score,
      max: dim.max,
    };
  });

  return (
    <div className={className} aria-label="Score across seven supervision dimensions">
      <ResponsiveContainer width="100%" height={320}>
        <RadarChart data={data} outerRadius="75%">
          <PolarGrid stroke="var(--color-border)" />
          <PolarAngleAxis
            dataKey="dimension"
            tick={{ fill: "var(--color-muted-foreground)", fontSize: 11 }}
          />
          <PolarRadiusAxis
            angle={90}
            domain={[0, 100]}
            tick={{ fill: "var(--color-muted-foreground)", fontSize: 10 }}
          />
          <Radar
            name="Score"
            dataKey="value"
            stroke="var(--color-primary)"
            fill="var(--color-primary)"
            fillOpacity={0.18}
            isAnimationActive
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "var(--color-surface)",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(value: number, _name, entry) => {
              const payload = entry.payload as { raw: number; max: number };
              return [`${payload.raw} / ${payload.max}`, "Score"];
            }}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * Textual fallback rendered when the recharts chart fails. Mirrors the
 * radar's information so the user still sees per-dimension scores — and is
 * accessibility-friendlier than the chart for screen-reader users.
 */
function ScoreRadarFallback({ dimensions }: { dimensions: ScoreBreakdown }) {
  const rows = (Object.keys(LABELS) as (keyof ScoreBreakdown)[]).map((key) => {
    const dim = dimensions[key];
    const pct = dim.max > 0 ? Math.round((dim.score / dim.max) * 100) : 0;
    return { label: LABELS[key], score: dim.score, max: dim.max, pct };
  });
  return (
    <table
      className="w-full text-left text-sm"
      aria-label="Score across seven supervision dimensions"
      data-testid="score-radar-fallback"
    >
      <thead>
        <tr className="text-[var(--color-muted-foreground)]">
          <th scope="col" className="pb-2 font-medium">Dimension</th>
          <th scope="col" className="pb-2 text-right font-medium">Score</th>
          <th scope="col" className="pb-2 text-right font-medium">%</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.label} className="border-t border-[var(--color-border)]">
            <td className="py-1.5">{row.label}</td>
            <td className="py-1.5 text-right tabular-nums">
              {row.score} / {row.max}
            </td>
            <td className="py-1.5 text-right tabular-nums">{row.pct}%</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * Tiny inline error boundary scoped to the radar chart. Implemented as a
 * class component because hooks can't catch errors from descendant renders.
 * We avoid `react-error-boundary` to keep the dependency surface lean.
 */
interface RadarErrorBoundaryProps {
  fallback: React.ReactNode;
  children: React.ReactNode;
}

interface RadarErrorBoundaryState {
  hasError: boolean;
}

class RadarErrorBoundary extends React.Component<
  RadarErrorBoundaryProps,
  RadarErrorBoundaryState
> {
  override state: RadarErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): RadarErrorBoundaryState {
    return { hasError: true };
  }

  override componentDidCatch(error: unknown): void {
    // Visible in DevTools so we can investigate browser-specific recharts
    // failures without users having to file an issue.
    console.error("[score-radar] chart render failed; using text fallback", error);
  }

  override render(): React.ReactNode {
    if (this.state.hasError) return this.props.fallback;
    return this.props.children;
  }
}
