/**
 * P2 fix — DimensionBreakdown must not crash when a dimension is missing its
 * ``signals`` array.
 *
 * ReportView's ``hasAllDimensions`` only checks each dimension key is a
 * non-null object, not that ``signals`` is an array. The breakdown read
 * ``d.signals.length`` unconditionally, so a (partial / legacy) dimension
 * without ``signals`` threw ``Cannot read properties of undefined`` and took
 * the WHOLE report down. The fix guards with ``(d.signals?.length ?? 0) > 0``.
 */
import * as React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ScoreBreakdown, ScoreDimension } from "@arena/shared-types";
import { DimensionBreakdown } from "@/components/report/DimensionBreakdown";

function dim(
  score: number | null,
  max: number,
  signals?: string[],
): ScoreDimension {
  // Deliberately allow omitting ``signals`` to model a partial payload —
  // cast away the array requirement the way a legacy wire shape would.
  const d: Partial<ScoreDimension> = { score, max };
  if (signals !== undefined) d.signals = signals;
  return d as ScoreDimension;
}

describe("DimensionBreakdown — missing signals", () => {
  it("renders every dimension even when some omit the signals array", () => {
    const dimensions = {
      // ``signals`` entirely absent on the first dimension — this is the
      // shape that previously threw.
      final_correctness: dim(20, 30),
      verification: dim(5, 20, ["skipped verify"]),
      agent_review: dim(10, 15),
      prompt_quality: dim(7, 10, []),
      context_selection: dim(8, 10),
      safety: dim(10, 10, ["clean"]),
      diff_minimality: dim(5, 5),
    } as unknown as ScoreBreakdown;

    expect(() =>
      render(<DimensionBreakdown dimensions={dimensions} />),
    ).not.toThrow();

    // The full grid rendered: all seven dimension labels are present.
    expect(screen.getByText("Final patch correctness")).toBeInTheDocument();
    expect(screen.getByText("Verification discipline")).toBeInTheDocument();
    expect(screen.getByText("Diff minimality")).toBeInTheDocument();

    // The dimension that DID carry signals still renders its signal line…
    expect(screen.getByText("skipped verify")).toBeInTheDocument();
    // …and the signal-less dimension simply omits the line (no crash, no
    // stray "undefined" text).
    expect(screen.queryByText(/undefined/)).toBeNull();
  });
});
