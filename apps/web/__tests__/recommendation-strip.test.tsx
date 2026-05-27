/**
 * P1-2 — RecommendationStrip tests.
 *
 * Covers:
 *   - Renders the diagnosis copy + three ranked cards from a fixture.
 *   - Each card surfaces the per-mission ``why`` copy + language chip +
 *     "→ Start" link to the mission detail page.
 *   - Cold-start fixture (``weakest_dim == null``) renders the
 *     "// orientation" chip + the ladder copy.
 *   - Mount fires ``recommendation_shown`` with the expected payload.
 *   - Clicking a card fires ``recommendation_clicked`` with its position.
 */
import * as React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import type { RecommendationSet } from "@arena/shared-types";

vi.mock("next/link", () => ({
  __esModule: true,
  default: ({
    children,
    href,
    onClick,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
    onClick?: React.MouseEventHandler<HTMLAnchorElement>;
  } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} onClick={onClick} {...rest}>
      {children}
    </a>
  ),
}));

const trackMock = vi.fn();
vi.mock("@/lib/telemetry", () => ({
  track: (...args: unknown[]) => trackMock(...args),
}));

import { RecommendationStrip } from "@/components/profile/RecommendationStrip";

const WARM_FIXTURE: RecommendationSet = {
  weakest_dim: "agent_review",
  diagnosis:
    "Your weakest dimension is Agent Review. Try these three in order — each forces a habit you're skipping.",
  recommendations: [
    {
      mission_id: "agent-picked-wrong-file",
      title: "02 · Agent picked the wrong file",
      language: "typescript",
      difficulty: "intermediate",
      why: "exercises agent_review through a misnamed function",
      your_best_score: 64,
      your_attempts: 1,
      status: "shipped",
    },
    {
      mission_id: "excessive-rewrite",
      title: "06 · Excessive rewrite under the guise of a fix",
      language: "python",
      difficulty: "intermediate",
      why: "exercises agent_review through diff scrutiny",
      your_best_score: null,
      your_attempts: 0,
      status: "shipped",
    },
    {
      mission_id: "api-contract-drift",
      title: "09 · API contract drift",
      language: "go",
      difficulty: "advanced",
      why: "exercises agent_review + verification on signature changes",
      your_best_score: null,
      your_attempts: 0,
      status: "shipped",
    },
  ],
  computed_at: "2026-05-27T12:00:00Z",
  cache_hit: true,
};

const COLD_FIXTURE: RecommendationSet = {
  weakest_dim: null,
  diagnosis: "Welcome — try the first three missions in order to get oriented.",
  recommendations: [
    {
      mission_id: "tutorial-01",
      title: "01 · Hello, supervisor",
      language: "typescript",
      difficulty: "beginner",
      why: "warm-up: walk a real failure mode through the agent loop",
      your_best_score: null,
      your_attempts: 0,
      status: "shipped",
    },
    {
      mission_id: "tutorial-02",
      title: "02 · Agent picked the wrong file",
      language: "typescript",
      difficulty: "beginner",
      why: "second rung: practise reading the diff before applying",
      your_best_score: null,
      your_attempts: 0,
      status: "shipped",
    },
    {
      mission_id: "tutorial-03",
      title: "03 · Tests pass, behaviour broken",
      language: "typescript",
      difficulty: "beginner",
      why: "third rung: catch the silent regression",
      your_best_score: null,
      your_attempts: 0,
      status: "shipped",
    },
  ],
  computed_at: "2026-05-27T12:00:00Z",
  cache_hit: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  // FE-P4 audit fix — ``recommendation_shown`` is now deduped via
  // sessionStorage so a navigate-away-and-back doesn't double-fire.
  // Each test starts with a clean slate so the dedupe gate doesn't
  // suppress the event we're asserting on.
  try {
    window.sessionStorage.clear();
  } catch {
    // ignore — sessionStorage may be unavailable in some jsdom variants
  }
});

describe("RecommendationStrip (P1-2)", () => {
  it("renders the diagnosis and three cards from the warm fixture", () => {
    render(<RecommendationStrip data={WARM_FIXTURE} />);

    expect(screen.getByText(/your next step/i)).toBeInTheDocument();
    expect(
      screen.getByTestId("recommendation-strip-diagnosis"),
    ).toHaveTextContent(/Your weakest dimension is Agent Review/i);

    const cards = screen.getAllByTestId("recommendation-card");
    expect(cards.length).toBe(3);
    expect(cards[0]).toHaveAttribute(
      "href",
      "/missions/agent-picked-wrong-file",
    );
    expect(cards[1]).toHaveAttribute("href", "/missions/excessive-rewrite");
    expect(cards[2]).toHaveAttribute("href", "/missions/api-contract-drift");

    // Per-mission why prose renders inside each card.
    for (const item of WARM_FIXTURE.recommendations) {
      expect(screen.getByText(item.why)).toBeInTheDocument();
    }

    // Best-score chip on the attempted mission; "not yet attempted" on the rest.
    const bestScoreNodes = screen.getAllByTestId(
      "recommendation-card-best-score",
    );
    expect(bestScoreNodes.length).toBe(1);
    expect(bestScoreNodes[0]).toHaveTextContent(/your best/i);
    expect(bestScoreNodes[0]).toHaveTextContent(/64/);

    // Language chips reflect the three sandbox languages.
    const chips = screen.getAllByTestId("recommendation-card-language");
    expect(chips.map((el) => el.textContent)).toEqual([
      "// ts",
      "// py",
      "// go",
    ]);

    // // orientation chip is NOT rendered on the warm path.
    expect(
      screen.queryByTestId("recommendation-strip-orientation-chip"),
    ).toBeNull();
  });

  it("fires recommendation_shown on mount with the warm payload", () => {
    render(<RecommendationStrip data={WARM_FIXTURE} />);

    expect(trackMock).toHaveBeenCalledWith("recommendation_shown", {
      kind: "profile",
      weakest_dim: "agent_review",
      mission_ids: [
        "agent-picked-wrong-file",
        "excessive-rewrite",
        "api-contract-drift",
      ],
      signed_in: true,
    });
  });

  it("fires recommendation_clicked with position on card click", () => {
    render(<RecommendationStrip data={WARM_FIXTURE} />);
    const cards = screen.getAllByTestId("recommendation-card");
    const target = cards[1];
    if (!target) throw new Error("expected a second recommendation card");
    fireEvent.click(target);
    expect(trackMock).toHaveBeenCalledWith("recommendation_clicked", {
      position: 1,
      mission_id: "excessive-rewrite",
      kind: "profile",
    });
  });

  it("renders the cold-start ladder copy + // orientation chip", () => {
    render(<RecommendationStrip data={COLD_FIXTURE} />);

    expect(
      screen.getByTestId("recommendation-strip-orientation-chip"),
    ).toBeInTheDocument();

    // The ladder copy lives directly underneath the diagnosis paragraph.
    expect(screen.getByText(/Start the ladder\./i)).toBeInTheDocument();

    // Each ladder card surfaces the "// orientation" chip in the header
    // (since weakest_dim is null we don't have a best-score affordance).
    const orientationChips = screen.getAllByTestId(
      "recommendation-card-orientation",
    );
    expect(orientationChips.length).toBe(3);

    // The shown event reflects the cold weakest_dim.
    expect(trackMock).toHaveBeenCalledWith("recommendation_shown", {
      kind: "profile",
      weakest_dim: null,
      mission_ids: ["tutorial-01", "tutorial-02", "tutorial-03"],
      signed_in: true,
    });
  });

  it("renders the // all clear chip + sharp-edge copy on the all-graded path (FE-P4)", () => {
    // ``_all_graded()`` in the engine returns ``weakest_dim = null``
    // plus a set of fully-graded shipped missions (``your_attempts >= 1``).
    // Previously the FE collapsed this case into cold-start, which lied
    // to a user who had already finished every mission. The new
    // discriminator splits the modes and renders distinct chips.
    const ALL_GRADED_FIXTURE: RecommendationSet = {
      weakest_dim: null,
      diagnosis:
        "You've finished every mission. These three are the freshest entries — replay one to keep the edge sharp.",
      recommendations: [
        {
          mission_id: "api-contract-drift",
          title: "09 · API contract drift",
          language: "go",
          difficulty: "advanced",
          why: "freshest mission — your last play was 12 days ago",
          your_best_score: 88,
          your_attempts: 3,
          status: "shipped",
        },
        {
          mission_id: "excessive-rewrite",
          title: "06 · Excessive rewrite under the guise of a fix",
          language: "python",
          difficulty: "intermediate",
          why: "freshest mission — replay to recalibrate",
          your_best_score: 91,
          your_attempts: 2,
          status: "shipped",
        },
      ],
      computed_at: "2026-05-27T12:00:00Z",
      cache_hit: false,
    };

    render(<RecommendationStrip data={ALL_GRADED_FIXTURE} />);

    // ``data-testid="rec-strip-mode"`` is the testability contract
    // for the engine path discriminator.
    const modeChip = screen.getByTestId("rec-strip-mode");
    expect(modeChip).toHaveAttribute("data-mode", "all-graded");
    expect(modeChip).toHaveTextContent("all-graded");

    // The all-graded chip surfaces "// all clear" instead of the
    // cold-start "// orientation" chip.
    expect(
      screen.getByTestId("recommendation-strip-all-clear-chip"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("recommendation-strip-orientation-chip"),
    ).toBeNull();

    // Sharp-edge copy lives underneath the diagnosis.
    expect(
      screen.getByText(/finished the ladder/i),
    ).toBeInTheDocument();

    // ``recommendation-card-orientation`` chip belongs to cold-start
    // only — must NOT render on the all-graded path.
    expect(
      screen.queryAllByTestId("recommendation-card-orientation").length,
    ).toBe(0);
  });

  it("warm-path discriminator stamps data-mode=normal on the strip", () => {
    render(<RecommendationStrip data={WARM_FIXTURE} />);
    const modeChip = screen.getByTestId("rec-strip-mode");
    expect(modeChip).toHaveAttribute("data-mode", "normal");
  });

  it("cold-start fixture stamps data-mode=cold-start on the strip", () => {
    render(<RecommendationStrip data={COLD_FIXTURE} />);
    const modeChip = screen.getByTestId("rec-strip-mode");
    expect(modeChip).toHaveAttribute("data-mode", "cold-start");
  });

  it("renders a coming-soon recommendation slot for placeholder items", () => {
    const head = WARM_FIXTURE.recommendations[0];
    if (!head) throw new Error("WARM_FIXTURE must have at least one item");
    const mixed: RecommendationSet = {
      ...WARM_FIXTURE,
      recommendations: [
        head,
        {
          mission_id: "future-mission",
          title: "10 · Coming soon",
          language: "go",
          difficulty: "advanced",
          why: "placeholder slot when every shipped mission is already graded",
          your_best_score: null,
          your_attempts: 0,
          status: "coming_soon",
          target_release_date: "2026-09-15",
        },
      ],
    };
    render(<RecommendationStrip data={mixed} />);

    const comingSoonCard = screen.getByTestId(
      "recommendation-card-coming-soon",
    );
    expect(comingSoonCard).toHaveAttribute("data-mission-id", "future-mission");
    expect(within(comingSoonCard).getByText("2026-09-15")).toBeInTheDocument();
    expect(
      within(comingSoonCard).getByRole("link", { name: /watch repo/i }),
    ).toBeInTheDocument();
  });
});
