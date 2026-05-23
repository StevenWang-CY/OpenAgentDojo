"use client";

import * as React from "react";
import type {
  PublicProfile,
  RubricDimension,
  ScoreBreakdown,
  ScoreDimension,
} from "@arena/shared-types";
import { ScoreRadar } from "../report/ScoreRadar";

const DIMENSION_MAX: Record<RubricDimension, number> = {
  final_correctness: 30,
  verification: 15,
  agent_review: 15,
  prompt_quality: 10,
  context_selection: 10,
  safety: 10,
  diff_minimality: 10,
};

interface ProfileRadarProps {
  averages: PublicProfile["radar_averages"];
}

/**
 * Renders the per-dimension averages from a user's graded submissions
 * as a 7-axis radar. Reuses the report-page `ScoreRadar` after wrapping
 * each flat average into a `ScoreDimension` envelope so the axes share
 * the same canonical max-score normalisation.
 */
export function ProfileRadar({ averages }: ProfileRadarProps) {
  const present = Object.entries(averages).filter(
    ([, v]) => typeof v === "number",
  );
  if (present.length === 0) return null;

  const breakdown = (Object.keys(DIMENSION_MAX) as RubricDimension[]).reduce(
    (acc, dim) => {
      const avg = averages[dim];
      const dimension: ScoreDimension = {
        score: typeof avg === "number" ? Math.round(avg) : 0,
        max: DIMENSION_MAX[dim],
        signals: [],
      };
      acc[dim] = dimension;
      return acc;
    },
    {} as ScoreBreakdown,
  );

  return <ScoreRadar dimensions={breakdown} />;
}
