import * as React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { PublicProfile } from "@arena/shared-types";

vi.mock("next/link", () => ({
  __esModule: true,
  default: ({
    children,
    href,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
  } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const getProfile = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getProfile: (...args: unknown[]) => getProfile(...args) };
});

import { ProfileView } from "@/components/profile/ProfileView";

const FIXTURE: PublicProfile = {
  handle: "alice",
  display_name: "Alice Liddell",
  joined_at: "2026-01-12T00:00:00Z",
  badges: [
    {
      id: "regression-test-writer",
      title: "Regression Test Writer",
      description: "Added a regression test that matches the failure mode.",
      icon: "shield",
      earned_at: "2026-03-04T00:00:00Z",
      session_id: "session-1",
    },
  ],
  history: [
    {
      session_id: "session-1",
      mission_id: "auth-cookie-expiration",
      mission_title: "Expired Session Cookie Still Grants Access",
      completed_at: "2026-03-04T01:00:00Z",
      score: 78,
      difficulty: "intermediate",
    },
  ],
  radar_averages: {},
  total_missions: 1,
  best_score: 78,
};

function renderWithClient(node: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ProfileView", () => {
  it("renders the header, badge grid, and mission history table from mocked getProfile", async () => {
    getProfile.mockResolvedValue(FIXTURE);

    renderWithClient(<ProfileView handle="alice" />);

    await waitFor(() =>
      expect(screen.getByText(/Alice Liddell/)).toBeInTheDocument()
    );
    expect(getProfile).toHaveBeenCalledWith("alice", expect.anything());

    expect(screen.getByText(/@alice/)).toBeInTheDocument();
    expect(screen.getByText(/Regression Test Writer/)).toBeInTheDocument();
    // Two link surfaces per row (clickable <tr role="link"> + inner <Link> for
    // screen readers); assert at least one resolves to the mission detail href.
    const missionLinks = screen.getAllByRole("link", {
      name: /Expired Session Cookie Still Grants Access/i,
    });
    expect(missionLinks.length).toBeGreaterThanOrEqual(1);
    expect(
      missionLinks.some(
        (el) => el.getAttribute("href") === "/missions/auth-cookie-expiration"
      )
    ).toBe(true);
    // The score `78` appears as both the "Best score" stat and the row cell;
    // make sure at least two occurrences render so we know both surfaces are
    // populated, rather than asserting on a single ambiguous match.
    expect(screen.getAllByText("78").length).toBeGreaterThanOrEqual(2);
  });

  it("renders the 404 state when the backend returns 404", async () => {
    const { ApiError } = await import("@/lib/api");
    getProfile.mockRejectedValueOnce(
      new ApiError("Profile not found", 404, null)
    );

    renderWithClient(<ProfileView handle="ghost" />);

    await waitFor(() =>
      expect(screen.getByText(/Profile not found/i)).toBeInTheDocument()
    );
    expect(screen.getByText(/@ghost/)).toBeInTheDocument();
  });
});
