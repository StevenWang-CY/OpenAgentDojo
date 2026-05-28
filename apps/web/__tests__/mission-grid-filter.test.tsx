/**
 * P1-1 — MissionGrid catalog filter tests.
 *
 * Covers:
 *   - Renders a mix of TypeScript / Python / Go shipped missions plus a
 *     ``coming_soon`` placeholder row.
 *   - Filtering by language narrows both the shipped grid and the up-next
 *     row to the chosen language.
 *   - Filtering by the failure-mode dropdown narrows the shipped grid to
 *     missions carrying the matching tag and hides the coming-soon row
 *     (placeholders have no tags yet).
 *
 * The "// recommended" chip is owned by the P1-2 frontend agent; this
 * file deliberately does not assert on it. The MissionCard exposes an
 * optional ``recommended`` prop today as a no-op seam for that agent.
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

// next/link is jsdom-incompatible without this shim (it leans on RouterContext).
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

// ── Fixtures ─────────────────────────────────────────────────────────────────

function shipped(
  partial: Pick<Mission, "id" | "title" | "language"> & Partial<Mission>,
): Mission {
  return {
    short_description: `${partial.title} — pedagogical brief.`,
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

const TS_MISSION = shipped({
  id: "auth-cookie-expiration",
  title: "Expired Session Cookie Still Grants Access",
  language: "typescript",
  failure_mode_id: "checks_presence_not_expiration",
  tags: ["checks_presence_not_expiration", "lang:typescript"],
  category: "auth",
});

const PY_MISSION = shipped({
  id: "data-api-race",
  title: "Race in the Python ETL Worker",
  language: "python",
  failure_mode_id: "race_condition",
  tags: ["race_condition", "lang:python"],
  category: "api",
});

const GO_MISSION = shipped({
  id: "go-goroutine-leak",
  title: "Goroutine Leak on Early Return",
  language: "go",
  failure_mode_id: "goroutine_leak",
  tags: ["goroutine_leak", "lang:go"],
  category: "debugging",
  repo_pack_id: "go-orders-service",
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
  GO_MISSION,
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
  // Unauthenticated viewer — keeps OrientationBanner in its anon branch
  // so the test isn't entangled with /me state.
  me.mockRejectedValue(
    Object.assign(new Error("401"), { status: 401, name: "ApiError" }),
  );
  // P1-2 — default: no recommendations for the anonymous default path.
  // Tests that need a signed-in viewer override this fixture in-line.
  getMyRecommendations.mockRejectedValue(
    Object.assign(new Error("401"), { status: 401, name: "ApiError" }),
  );
  // FE-P4 audit fix — ``recommendation_shown`` is now deduped via
  // sessionStorage. Reset between tests so the catalog's dedupe gate
  // doesn't pin to a previous test's mission id.
  try {
    window.sessionStorage.clear();
  } catch {
    // ignore — sessionStorage may be unavailable
  }
});

describe("MissionGrid filters (P1-1)", () => {
  it("renders shipped TS/Py/Go missions plus a coming-soon up-next row", async () => {
    renderGrid();

    await waitFor(() =>
      expect(screen.getByText(TS_MISSION.title)).toBeInTheDocument(),
    );
    expect(screen.getByText(PY_MISSION.title)).toBeInTheDocument();
    expect(screen.getByText(GO_MISSION.title)).toBeInTheDocument();

    // Coming-soon row renders both placeholders (no filter active).
    const upcomingRow = screen.getByTestId("mission-grid-upcoming");
    expect(within(upcomingRow).getByText(UPCOMING_GO.title)).toBeInTheDocument();
    expect(within(upcomingRow).getByText(UPCOMING_PY.title)).toBeInTheDocument();

    // The // up next heading is present and the watch-repo link points at
    // the placeholder GitHub URL. The heading itself is rendered with a
    // dedicated id so we target it directly (the chip on each placeholder
    // card also reads "// up next" so a text-only match would be ambiguous).
    expect(
      within(upcomingRow).getByRole("heading", { name: /up next/i }),
    ).toBeInTheDocument();
    // Multiple "watch repo" links land in this row (one per card + the
    // section header link). Asserting at least one of them points at the
    // placeholder GitHub URL keeps the test stable while still proving the
    // env-driven constant flows through to the FE.
    const watchRepoLinks = within(upcomingRow).getAllByRole("link", {
      name: /watch repo/i,
    });
    expect(watchRepoLinks.length).toBeGreaterThan(0);
    for (const link of watchRepoLinks) {
      expect(link).toHaveAttribute(
        "href",
        "https://github.com/openagentdojo/openagentdojo",
      );
    }

    // Language chip shows up on each shipped card.
    const langChips = screen.getAllByTestId("mission-card-language");
    const chipText = langChips.map((el) => el.textContent);
    expect(chipText).toEqual(
      expect.arrayContaining(["// ts", "// py", "// go"]),
    );
  });

  it("filtering by Go shows only Go missions plus Go coming-soon entries", async () => {
    renderGrid();

    await waitFor(() =>
      expect(screen.getByText(TS_MISSION.title)).toBeInTheDocument(),
    );

    const langFilter = screen.getByTestId("language-filter");
    const goButton = within(langFilter).getByRole("tab", { name: "go" });
    fireEvent.click(goButton);

    // Shipped grid now contains only the Go mission.
    const shipped = screen.getByTestId("mission-grid-shipped");
    expect(within(shipped).getByText(GO_MISSION.title)).toBeInTheDocument();
    expect(within(shipped).queryByText(TS_MISSION.title)).toBeNull();
    expect(within(shipped).queryByText(PY_MISSION.title)).toBeNull();

    // Coming-soon row narrows to the Go placeholder.
    const upcoming = screen.getByTestId("mission-grid-upcoming");
    expect(within(upcoming).getByText(UPCOMING_GO.title)).toBeInTheDocument();
    expect(within(upcoming).queryByText(UPCOMING_PY.title)).toBeNull();
  });

  it("renders the // recommended chip on the single top recommendation for signed-in viewers", async () => {
    // Signed-in viewer + a live recommendation pointing at the Python
    // mission. Only that card should carry the chip; the other two
    // shipped cards stay clean so the signal is preserved.
    me.mockResolvedValueOnce({
      id: "11111111-1111-1111-1111-111111111111",
      email: "user@example.com",
      handle: "user",
      display_name: null,
      csrf_token: "csrf-token",
      created_at: "2026-01-01T00:00:00Z",
      tutorial_replay_count: 0,
    });
    getMyRecommendations.mockResolvedValueOnce({
      weakest_dim: "agent_review",
      diagnosis: "Try the Python race condition next.",
      recommendations: [
        {
          mission_id: PY_MISSION.id,
          title: PY_MISSION.title,
          language: "python",
          difficulty: "intermediate",
          why: "exercises agent_review",
          your_best_score: null,
          your_attempts: 0,
          status: "shipped",
        },
      ],
      computed_at: "2026-05-27T12:00:00Z",
      cache_hit: true,
    });

    renderGrid();

    await waitFor(() =>
      expect(screen.getByText(PY_MISSION.title)).toBeInTheDocument(),
    );

    const chips = await waitFor(() => {
      const matches = screen.getAllByTestId("mission-card-recommended-chip");
      expect(matches.length).toBeGreaterThan(0);
      return matches;
    });
    // Exactly ONE card carries the chip (the top recommendation).
    expect(chips.length).toBe(1);
    // And it's the Python mission card — walk up to the parent <a> and
    // assert the href points at it.
    const chip = chips[0];
    if (!chip) throw new Error("expected at least one recommendation chip");
    const chipCard = chip.closest("a");
    expect(chipCard).not.toBeNull();
    expect(chipCard).toHaveAttribute("href", `/missions/${PY_MISSION.id}`);
    expect(chipCard).toHaveAttribute("data-recommended", "true");
  });

  it("renders no // recommended chip when there are no recommendations", async () => {
    me.mockResolvedValueOnce({
      id: "11111111-1111-1111-1111-111111111111",
      email: "user@example.com",
      handle: "user",
      display_name: null,
      csrf_token: "csrf-token",
      created_at: "2026-01-01T00:00:00Z",
      tutorial_replay_count: 0,
    });
    getMyRecommendations.mockResolvedValueOnce({
      weakest_dim: null,
      diagnosis: "",
      recommendations: [],
      computed_at: "2026-05-27T12:00:00Z",
      cache_hit: false,
    });

    renderGrid();

    await waitFor(() =>
      expect(screen.getByText(TS_MISSION.title)).toBeInTheDocument(),
    );
    expect(screen.queryAllByTestId("mission-card-recommended-chip").length).toBe(
      0,
    );
  });

  it("surfaces a banner when the recommended mission is filtered out (FE-P4)", async () => {
    // Recommendation points at the Go mission. The user filters to
    // Python, which hides the Go card. Without the banner the chip
    // silently disappears; with the banner, the user sees the
    // affordance + a one-click "clear filters" recovery.
    me.mockResolvedValueOnce({
      id: "11111111-1111-1111-1111-111111111111",
      email: "user@example.com",
      handle: "user",
      display_name: null,
      csrf_token: "csrf-token",
      created_at: "2026-01-01T00:00:00Z",
      tutorial_replay_count: 0,
    });
    getMyRecommendations.mockResolvedValueOnce({
      weakest_dim: "agent_review",
      diagnosis: "Try the Go goroutine leak next.",
      recommendations: [
        {
          mission_id: GO_MISSION.id,
          title: GO_MISSION.title,
          language: "go",
          difficulty: "intermediate",
          why: "exercises agent_review on a goroutine leak",
          your_best_score: null,
          your_attempts: 0,
          status: "shipped",
        },
      ],
      computed_at: "2026-05-27T12:00:00Z",
      cache_hit: true,
    });

    renderGrid();

    // Wait for both the catalog AND the recommendation chip to land
    // — otherwise the banner check below races the recommendation
    // query settling.
    await waitFor(() =>
      expect(screen.getByText(GO_MISSION.title)).toBeInTheDocument(),
    );
    await waitFor(() => {
      expect(
        screen.queryAllByTestId("mission-card-recommended-chip").length,
      ).toBe(1);
    });

    // Default state: recommended mission is visible in the grid, no banner.
    expect(screen.queryByTestId("recommendation-hidden-banner")).toBeNull();

    // Narrow to Python — the Go recommendation drops out of the grid.
    const langFilter = screen.getByTestId("language-filter");
    const pyButton = within(langFilter).getByRole("tab", { name: "python" });
    fireEvent.click(pyButton);

    await waitFor(() => {
      expect(
        screen.getByTestId("recommendation-hidden-banner"),
      ).toBeInTheDocument();
    });
    const banner = screen.getByTestId("recommendation-hidden-banner");
    expect(banner).toHaveAttribute("data-mission-id", GO_MISSION.id);
    expect(banner).toHaveTextContent(GO_MISSION.title);

    // Clicking the CTA resets filters; the Go mission comes back into
    // the grid and the banner disappears.
    const clearCta = within(banner).getByTestId(
      "recommendation-hidden-clear-filters",
    );
    fireEvent.click(clearCta);

    await waitFor(() => {
      const shippedGrid = screen.getByTestId("mission-grid-shipped");
      expect(within(shippedGrid).getByText(GO_MISSION.title)).toBeInTheDocument();
    });
    expect(screen.queryByTestId("recommendation-hidden-banner")).toBeNull();
  });

  it("no banner for anonymous viewers even when a recommendation exists in cache", async () => {
    // Anonymous viewer (default beforeEach) — even if some other surface
    // populated the cache with a recommendation, the banner gate also
    // requires ``signedIn === true`` so the unsigned path stays quiet.
    renderGrid();
    await waitFor(() =>
      expect(screen.getByText(TS_MISSION.title)).toBeInTheDocument(),
    );
    const langFilter = screen.getByTestId("language-filter");
    const pyButton = within(langFilter).getByRole("tab", { name: "python" });
    fireEvent.click(pyButton);
    expect(screen.queryByTestId("recommendation-hidden-banner")).toBeNull();
  });

  it("filtering by failure mode narrows to missions carrying the matching tag", async () => {
    renderGrid();

    await waitFor(() =>
      expect(screen.getByText(TS_MISSION.title)).toBeInTheDocument(),
    );

    const select = screen.getByLabelText(/failure mode/i) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "race_condition" } });

    const shipped = screen.getByTestId("mission-grid-shipped");
    expect(within(shipped).getByText(PY_MISSION.title)).toBeInTheDocument();
    expect(within(shipped).queryByText(TS_MISSION.title)).toBeNull();
    expect(within(shipped).queryByText(GO_MISSION.title)).toBeNull();

    // P1 audit fix — coming-soon placeholders carry no tags yet, but the
    // row still renders with a hint chip so the roadmap stays discoverable
    // while a failure-mode filter is active. Previously the row collapsed
    // to empty, which hid the roadmap from anyone narrowing by failure
    // mode (a discoverability regression).
    const upcomingRow = screen.getByTestId("mission-grid-upcoming");
    expect(
      within(upcomingRow).getByTestId("mission-grid-upcoming-filter-hint"),
    ).toBeInTheDocument();
    expect(within(upcomingRow).getByText(UPCOMING_GO.title)).toBeInTheDocument();
    expect(within(upcomingRow).getByText(UPCOMING_PY.title)).toBeInTheDocument();
  });
});
