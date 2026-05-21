"use client";

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
