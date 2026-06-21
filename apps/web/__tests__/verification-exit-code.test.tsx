/**
 * P1 — the three workspace verification predicates must require
 * ``exit_code === 0``. A ``command.run`` that exits 127 (e.g. ``pnpm`` missing
 * in a Go/Python sandbox) must NOT grant "verification discipline" credit;
 * an ``exit_code: 0`` run resolves the signal. The backend grader already
 * gates on a clean exit — this aligns the FE signals.
 *
 * Covers all three predicate surfaces:
 *   - VerificationChecklist ("I ran the test suite"/"…typecheck or lint")
 *   - ScorePreview's Verification signal
 *   - summarisePillSignals (the WorkspaceTopBar pill tally)
 */

import * as React from "react";
import { render, screen, within } from "@testing-library/react";
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

import { VerificationChecklist } from "@/components/workspace/VerificationChecklist";
import { ScorePreview } from "@/components/workspace/ScorePreview";
import { WorkspaceTopBar } from "@/components/workspace/WorkspaceTopBar";

let nextId = 1;
function commandRun(
  category: "test" | "typecheck" | "lint",
  exit_code: number
): SupervisionEvent {
  return {
    id: nextId++,
    session_id: "session-1",
    event_type: "command.run",
    occurred_at: "2026-06-21T10:00:00Z",
    payload: { command: "x", category, exit_code },
  };
}

describe("VerificationChecklist — exit_code gate", () => {
  it("keeps the 'ran tests' item unchecked when the test command exited 127", () => {
    render(<VerificationChecklist events={[commandRun("test", 127)]} />);
    const item = screen.getByText(/I ran the test suite/i).closest("li")!;
    // The checked state uses CheckCircle2 vs Circle — the muted foreground
    // class marks the unchecked label.
    expect(item.querySelector("svg")).toBeTruthy();
    expect(within(item).getByText(/I ran the test suite/i).className).toMatch(
      /muted-foreground/
    );
  });

  it("checks both verification items only when a command exited 0", () => {
    render(
      <VerificationChecklist
        events={[commandRun("test", 0), commandRun("lint", 0)]}
      />
    );
    const testItem = screen.getByText(/I ran the test suite/i).closest("li")!;
    const tcItem = screen.getByText(/typecheck or lint/i).closest("li")!;
    expect(within(testItem).getByText(/I ran the test suite/i).className).not.toMatch(
      /muted-foreground/
    );
    expect(within(tcItem).getByText(/typecheck or lint/i).className).not.toMatch(
      /muted-foreground/
    );
  });

  it("does not check the typecheck/lint item on a non-zero lint exit", () => {
    render(<VerificationChecklist events={[commandRun("lint", 1)]} />);
    const tcItem = screen.getByText(/typecheck or lint/i).closest("li")!;
    expect(within(tcItem).getByText(/typecheck or lint/i).className).toMatch(
      /muted-foreground/
    );
  });
});

describe("ScorePreview — Verification signal exit_code gate", () => {
  it("reports 'tests not yet run' when the only command exited 127", () => {
    render(
      <ScorePreview
        expectedRequiredContext={[]}
        selectedContext={[]}
        events={[commandRun("test", 127)]}
      />
    );
    expect(screen.getByText(/tests not yet run/i)).toBeInTheDocument();
    expect(screen.queryByText(/checks? run/i)).not.toBeInTheDocument();
  });

  it("counts only the clean-exit checks", () => {
    render(
      <ScorePreview
        expectedRequiredContext={[]}
        selectedContext={[]}
        events={[
          commandRun("test", 127),
          commandRun("typecheck", 0),
          commandRun("lint", 0),
        ]}
      />
    );
    // Two of the three ran cleanly.
    expect(screen.getByText(/2 checks run/i)).toBeInTheDocument();
  });
});

describe("summarisePillSignals — exit_code gate (via WorkspaceTopBar pill)", () => {
  function pillCount(events: SupervisionEvent[]): string {
    render(
      <WorkspaceTopBar
        sessionId="session-1"
        missionId="m"
        missionTitle="Mission"
        difficulty="beginner"
        sandboxDriver="docker"
        events={events}
        expectedRequiredContext={[]}
        selectedContext={[]}
        sessionMode="self_study"
        integritySignalsCount={0}
      />
    );
    const pill = screen.getByTestId("score-pill");
    return pill.textContent ?? "";
  }

  it("does not count a 127 test run toward the pill tally", () => {
    // Context-required is empty (auto-ok = 1 signal); a 127 verification
    // must NOT add a second. So 1/4.
    expect(pillCount([commandRun("test", 127)])).toMatch(/1\/4/);
  });

  it("counts a clean test run toward the pill tally", () => {
    // Empty required (1) + clean verification (1) = 2/4.
    expect(pillCount([commandRun("test", 0)])).toMatch(/2\/4/);
  });
});
