/**
 * P1 audit fix — MissionGrid keeps the "// up next" row visible when a
 * failure-mode filter is active.
 *
 * Previously the upcoming row collapsed to empty whenever
 * ``activeFailureMode !== "all"`` (placeholders carry no tags). That hid
 * the roadmap from anyone narrowing by failure mode, which is a
 * discoverability regression. The new behaviour:
 *
 *   - The upcoming row still renders.
 *   - A small monospace hint chip appears clarifying that upcoming
 *     missions don't carry failure-mode tags yet.
 */
import * as React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Mission } from "@arena/shared-types";

vi.mock("next/link", () => ({
  __esModule: true,
  default: ({
    children,
    href,
    ...rest
  }: { children: React.ReactNode; href: string } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const listMissions = vi.fn();
const me = vi.fn();
const getMyRecommendations = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listMissions: (...args: unknown[]) => listMissions(...args),
    getMyRecommendations: (...args: unknown[]) => getMyRecommendations(...args),
    auth: {
      ...actual.auth,
      me: (...args: unknown[]) => me(...args),
    },
  };
});

import { MissionGrid } from "@/components/catalog/MissionGrid";

function shipped(
  partial: Pick<Mission, "id" | "title" | "language"> & Partial<Mission>,
): Mission {
  return {
    short_description: `${partial.title} — brief.`,
    difficulty: "intermediate",
    category: "debugging",
    estimated_minutes: 20,
    failure_mode_id: partial.failure_mode_id ?? "wrong_layer_committed",
    skills_tested: [],
    version: 1,
    published: true,
    kind: "standard",
    repo_pack_id: partial.repo_pack_id ?? "fullstack-auth-demo",
    tags: partial.tags ?? [],
    status: "shipped",
    target_release_date: null,
    ...partial,
  };
}

function comingSoon(
  partial: Pick<Mission, "id" | "title" | "language" | "target_release_date">,
): Mission {
  return {
    short_description: `${partial.title} — coming soon.`,
    difficulty: "beginner",
    category: "debugging",
    estimated_minutes: 0,
    failure_mode_id: "",
    skills_tested: [],
    version: 1,
    published: false,
    kind: "standard",
    repo_pack_id: null,
    tags: [],
    status: "coming_soon",
    ...partial,
  };
}

const PY_MISSION = shipped({
  id: "data-api-race",
  title: "Race in the Python ETL Worker",
  language: "python",
  failure_mode_id: "race_condition",
  tags: ["race_condition", "lang:python"],
  category: "api",
});

const TS_MISSION = shipped({
  id: "auth-cookie-expiration",
  title: "Expired Session Cookie Still Grants Access",
  language: "typescript",
  failure_mode_id: "checks_presence_not_expiration",
  tags: ["checks_presence_not_expiration", "lang:typescript"],
  category: "auth",
});

const UPCOMING_GO = comingSoon({
  id: "go-request-deadline-stripped",
  title: "Request Deadline Stripped on Refactor",
  language: "go",
  target_release_date: "2026-07-01",
});

const UPCOMING_PY = comingSoon({
  id: "py-bad-fixture-overfit",
  title: "Fixture Overfit Hides the Bug",
  language: "python",
  target_release_date: "2026-08-15",
});

const ALL_MISSIONS: Mission[] = [
  TS_MISSION,
  PY_MISSION,
  UPCOMING_GO,
  UPCOMING_PY,
];

function renderGrid() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MissionGrid />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listMissions.mockResolvedValue(ALL_MISSIONS);
  me.mockRejectedValue(
    Object.assign(new Error("401"), { status: 401, name: "ApiError" }),
  );
  getMyRecommendations.mockRejectedValue(
    Object.assign(new Error("401"), { status: 401, name: "ApiError" }),
  );
  try {
    window.sessionStorage.clear();
  } catch {
    // ignore — sessionStorage may be unavailable
  }
});

describe("MissionGrid upcoming row with failure-mode filter (P1 audit)", () => {
  it("keeps the // up next row visible with a hint chip when a failure-mode filter is active", async () => {
    renderGrid();

    await waitFor(() =>
      expect(screen.getByText(PY_MISSION.title)).toBeInTheDocument(),
    );

    // No filter yet — hint chip is absent.
    expect(
      screen.queryByTestId("mission-grid-upcoming-filter-hint"),
    ).toBeNull();

    // Narrow by failure mode.
    const select = screen.getByLabelText(/failure mode/i) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "race_condition" } });

    // Shipped grid narrows to the matching mission.
    const shippedGrid = screen.getByTestId("mission-grid-shipped");
    expect(within(shippedGrid).getByText(PY_MISSION.title)).toBeInTheDocument();
    expect(within(shippedGrid).queryByText(TS_MISSION.title)).toBeNull();

    // Upcoming row stays mounted — both placeholders still render.
    const upcomingRow = screen.getByTestId("mission-grid-upcoming");
    expect(within(upcomingRow).getByText(UPCOMING_GO.title)).toBeInTheDocument();
    expect(within(upcomingRow).getByText(UPCOMING_PY.title)).toBeInTheDocument();

    // Hint chip clarifies that placeholders aren't tagged with failure
    // modes yet.
    const hint = within(upcomingRow).getByTestId(
      "mission-grid-upcoming-filter-hint",
    );
    expect(hint).toBeInTheDocument();
    expect(hint).toHaveTextContent(/upcoming missions/i);
    expect(hint).toHaveTextContent(/failure-mode/i);
  });

  it("hint chip is hidden once the filter is cleared", async () => {
    renderGrid();

    await waitFor(() =>
      expect(screen.getByText(PY_MISSION.title)).toBeInTheDocument(),
    );

    const select = screen.getByLabelText(/failure mode/i) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "race_condition" } });

    expect(
      screen.getByTestId("mission-grid-upcoming-filter-hint"),
    ).toBeInTheDocument();

    fireEvent.change(select, { target: { value: "all" } });

    expect(
      screen.queryByTestId("mission-grid-upcoming-filter-hint"),
    ).toBeNull();
    const upcomingRow = screen.getByTestId("mission-grid-upcoming");
    expect(within(upcomingRow).getByText(UPCOMING_GO.title)).toBeInTheDocument();
    expect(within(upcomingRow).getByText(UPCOMING_PY.title)).toBeInTheDocument();
  });
});
