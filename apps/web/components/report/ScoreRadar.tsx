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
  final_correctness: "correctness",
  verification: "verification",
  agent_review: "agent review",
  prompt_quality: "prompt",
  context_selection: "context",
  safety: "safety",
  diff_minimality: "minimality",
};

const MONO_STACK =
  '"JetBrains Mono", "SF Mono", ui-monospace, Menlo, Consolas, monospace';

export function ScoreRadar({ dimensions, className }: ScoreRadarProps) {
  return (
    <RadarErrorBoundary
      fallback={<ScoreRadarFallback dimensions={dimensions} />}
    >
      <ScoreRadarChart dimensions={dimensions} className={className} />
    </RadarErrorBoundary>
  );
}

function ScoreRadarChart({ dimensions, className }: ScoreRadarProps) {
  const data = (Object.keys(LABELS) as (keyof ScoreBreakdown)[]).map((key) => {
    const dim: ScoreDimension = dimensions[key];
    // Pending dimensions (score=null) plot as 0 on the radar — the
    // diagnostic narrative renders "pending" labels elsewhere, so the
    // axis still shows up but doesn't pretend a number it doesn't have.
    const safeScore = dim.score ?? 0;
    return {
      dimension: LABELS[key],
      value: dim.max > 0 ? (safeScore / dim.max) * 100 : 0,
      raw: dim.score,
      max: dim.max,
    };
  });

  return (
    <div
      className={className}
      aria-label="Score across seven supervision dimensions"
    >
      <ResponsiveContainer width="100%" height={300}>
        <RadarChart data={data} outerRadius="72%">
          <PolarGrid
            stroke="var(--color-border)"
            strokeWidth={1}
            gridType="polygon"
          />
          <PolarAngleAxis
            dataKey="dimension"
            tick={{
              fill: "var(--color-muted-foreground)",
              fontSize: 10.5,
              fontFamily: MONO_STACK,
              letterSpacing: "0.04em",
            }}
          />
          <PolarRadiusAxis
            angle={90}
            domain={[0, 100]}
            tick={{
              fill: "var(--color-muted-foreground)",
              fontSize: 9,
              fontFamily: MONO_STACK,
              opacity: 0.7,
            }}
            tickCount={5}
            stroke="transparent"
          />
          <Radar
            name="Score"
            dataKey="value"
            stroke="var(--color-foreground)"
            strokeWidth={1.25}
            fill="var(--color-foreground)"
            fillOpacity={0.08}
            dot={{
              r: 2.5,
              fill: "var(--color-surface)",
              stroke: "var(--color-foreground)",
              strokeWidth: 1.25,
            }}
            isAnimationActive
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "var(--color-surface)",
              border: "1px solid var(--color-border)",
              borderRadius: 6,
              fontSize: 12,
              fontFamily: MONO_STACK,
            }}
            formatter={(value: number, _name, entry) => {
              const payload = entry.payload as { raw: number; max: number };
              return [`${payload.raw} / ${payload.max}`, "score"];
            }}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}

function ScoreRadarFallback({ dimensions }: { dimensions: ScoreBreakdown }) {
  const rows = (Object.keys(LABELS) as (keyof ScoreBreakdown)[]).map((key) => {
    const dim = dimensions[key];
    const safe = dim.score ?? 0;
    const pct = dim.max > 0 ? Math.round((safe / dim.max) * 100) : 0;
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
          <th scope="col" className="pb-2 font-medium">
            Dimension
          </th>
          <th scope="col" className="pb-2 text-right font-medium">
            Score
          </th>
          <th scope="col" className="pb-2 text-right font-medium">
            %
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr
            key={row.label}
            className="border-t border-[var(--color-border)]"
          >
            <td className="py-1.5 font-mono">{row.label}</td>
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
    console.error(
      "[score-radar] chart render failed; using text fallback",
      error,
    );
  }

  override render(): React.ReactNode {
    if (this.state.hasError) return this.props.fallback;
    return this.props.children;
  }
}
