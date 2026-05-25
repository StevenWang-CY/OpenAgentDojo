/**
 * P0-8 — WorkspaceTopBar renders the honor-mode banner for self-study
 * sessions and the proctored chip (with live count) for proctored ones.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { WorkspaceTopBar } from "@/components/workspace/WorkspaceTopBar";

const SESSION = "11111111-2222-3333-4444-555555555555";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => `/workspace/${SESSION}`,
}));

vi.mock("@/lib/telemetry", () => ({
  track: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
    message: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock("@tanstack/react-query", () => ({
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQuery: () => ({ data: undefined, isPending: false, isLoading: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

describe("WorkspaceTopBar — anti-cheating posture (P0-8)", () => {
  it("renders the honor-mode banner when session is self_study", () => {
    render(
      <WorkspaceTopBar
        sessionId={SESSION}
        missionId="mission"
        missionTitle="Test mission"
        difficulty="beginner"
        sandboxDriver="docker"
        events={[]}
        expectedRequiredContext={[]}
        selectedContext={[]}
        sessionMode="self_study"
        integritySignalsCount={0}
      />,
    );
    expect(screen.getByTestId("honor-mode-banner")).toBeInTheDocument();
    expect(screen.queryByTestId("proctored-mode-chip")).not.toBeInTheDocument();
    expect(screen.getByText(/practice only/i)).toBeInTheDocument();
  });

  it("renders the proctored chip and live signal count when proctored", () => {
    render(
      <WorkspaceTopBar
        sessionId={SESSION}
        missionId="mission"
        missionTitle="Test mission"
        difficulty="beginner"
        sandboxDriver="docker"
        events={[]}
        expectedRequiredContext={[]}
        selectedContext={[]}
        sessionMode="proctored"
        integritySignalsCount={3}
      />,
    );
    expect(screen.getByTestId("proctored-mode-chip")).toBeInTheDocument();
    expect(screen.queryByTestId("honor-mode-banner")).not.toBeInTheDocument();
    expect(screen.getByText(/proctored/)).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText(/integrity signal/i)).toBeInTheDocument();
  });

  it("singularises 'signal' on count == 1", () => {
    render(
      <WorkspaceTopBar
        sessionId={SESSION}
        missionId="mission"
        missionTitle="Test mission"
        difficulty="beginner"
        sandboxDriver="docker"
        events={[]}
        expectedRequiredContext={[]}
        selectedContext={[]}
        sessionMode="proctored"
        integritySignalsCount={1}
      />,
    );
    expect(screen.getByText(/integrity signal\b/i)).toBeInTheDocument();
    expect(screen.queryByText(/integrity signals\b/i)).not.toBeInTheDocument();
  });
});
