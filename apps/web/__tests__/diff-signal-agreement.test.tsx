/**
 * P2 — the top-bar pill's Diff signal must agree with ScorePreview's Diff
 * signal. A supervisor who HAND-EDITS files (the core workflow) produces a
 * non-empty diff but no ``patch.applied`` event. Previously the pill keyed
 * its Diff signal off ``patch.applied`` while ScorePreview keyed off the
 * changed-files set, so the pill showed Diff not-done while the panel it
 * expands showed it green — a visible contradiction. Both now read the same
 * changed-files source.
 */

import * as React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SupervisionEvent } from "@arena/shared-types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/workspace/session-1",
}));

vi.mock("@/lib/telemetry", () => ({ track: vi.fn() }));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock("@tanstack/react-query", () => ({
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQuery: () => ({ data: undefined, isPending: false, isLoading: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

import { ScorePreview } from "@/components/workspace/ScorePreview";
import { WorkspaceTopBar } from "@/components/workspace/WorkspaceTopBar";

// Hand-edits change files but never emit patch.applied.
const HAND_EDIT_EVENTS: SupervisionEvent[] = [];
const CHANGED_FILES = ["src/auth/session.ts"];

describe("Diff signal — pill agrees with ScorePreview on hand edits", () => {
  it("ScorePreview reports the diff as changed when changedFiles is non-empty", () => {
    render(
      <ScorePreview
        expectedRequiredContext={[]}
        selectedContext={[]}
        events={HAND_EDIT_EVENTS}
        changedFiles={CHANGED_FILES}
      />
    );
    expect(screen.getByText(/Diff: 1 file changed/i)).toBeInTheDocument();
  });

  it("the top-bar pill counts the Diff signal with zero patch.applied", () => {
    render(
      <WorkspaceTopBar
        sessionId="session-1"
        missionId="m"
        missionTitle="Mission"
        difficulty="beginner"
        sandboxDriver="docker"
        events={HAND_EDIT_EVENTS}
        expectedRequiredContext={[]}
        selectedContext={[]}
        diffChangedFiles={CHANGED_FILES}
        sessionMode="self_study"
        integritySignalsCount={0}
      />
    );
    // Empty required-context (auto-ok = 1) + non-empty diff (1) = 2/4,
    // with no patch.applied event in the stream.
    expect(screen.getByTestId("score-pill").textContent ?? "").toMatch(/2\/4/);
  });

  it("the pill does not count the Diff signal when there are no changed files", () => {
    render(
      <WorkspaceTopBar
        sessionId="session-1"
        missionId="m"
        missionTitle="Mission"
        difficulty="beginner"
        sandboxDriver="docker"
        events={HAND_EDIT_EVENTS}
        expectedRequiredContext={[]}
        selectedContext={[]}
        diffChangedFiles={[]}
        sessionMode="self_study"
        integritySignalsCount={0}
      />
    );
    // Only the empty-required-context auto-ok signal counts: 1/4.
    expect(screen.getByTestId("score-pill").textContent ?? "").toMatch(/1\/4/);
  });
});
