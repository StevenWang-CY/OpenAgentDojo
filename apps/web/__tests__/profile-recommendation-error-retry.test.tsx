/**
 * P1 audit fix — ProfileView shows a retry surface when the
 * recommendation query errors.
 *
 * Previously the strip silently disappeared on 5xx / network errors,
 * leaving the owner with no signal that the strip exists. The new
 * behaviour:
 *
 *   - When ``recommendationsQuery.isError`` is true on an owner view,
 *     render a small monospace block with a retry button.
 *   - Clicking the button calls ``query.refetch()``.
 *   - The error reason is NOT surfaced to the DOM (it can contain
 *     backend URLs); it stays in ``console.error``.
 */
import * as React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PublicProfile } from "@arena/shared-types";

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

const getProfile = vi.fn();
const me = vi.fn();
const getMyRecommendations = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getProfile: (...args: unknown[]) => getProfile(...args),
    getMyRecommendations: (...args: unknown[]) => getMyRecommendations(...args),
    auth: {
      ...actual.auth,
      me: (...args: unknown[]) => me(...args),
    },
  };
});

import { ProfileView } from "@/components/profile/ProfileView";

const FIXTURE: PublicProfile = {
  handle: "alice",
  display_name: "Alice Liddell",
  joined_at: "2026-01-12T00:00:00Z",
  github_login: null,
  github_avatar_url: null,
  github_html_url: null,
  github_verified_at: null,
  badges: [],
  history: [],
  radar_averages: {},
  total_missions: 0,
  best_score: null,
  has_verified_attempts: false,
  verified_attempts_only: false,
};

function renderWithClient(node: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
        // Default retry delay is exponential (1s+); zero it out so
        // ``retry: failureCount < 1`` settles synchronously in tests.
        retryDelay: 0,
      },
    },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  try {
    window.sessionStorage.clear();
  } catch {
    // ignore
  }
});

describe("ProfileView recommendation error + retry (P1 audit)", () => {
  it("renders the retry surface when the recommendations query errors for the owner", async () => {
    getProfile.mockResolvedValue(FIXTURE);
    me.mockResolvedValue({
      id: "11111111-1111-1111-1111-111111111111",
      email: "alice@example.com",
      handle: "alice",
      display_name: "Alice Liddell",
      csrf_token: "csrf-token",
      created_at: "2026-01-12T00:00:00Z",
      tutorial_replay_count: 0,
    });
    const { ApiError } = await import("@/lib/api");
    // The query's own ``retry`` allows ONE retry on a 5xx (failureCount
    // < 1), so the query reaches ``isError`` only after two consecutive
    // failures. Reject the first two calls; the user-driven retry
    // (third call) then resolves successfully.
    getMyRecommendations
      .mockRejectedValueOnce(new ApiError("upstream timeout", 503, null))
      .mockRejectedValueOnce(new ApiError("upstream timeout", 503, null))
      .mockResolvedValueOnce({
        weakest_dim: null,
        diagnosis: "",
        recommendations: [],
        computed_at: "2026-05-27T12:00:00Z",
        cache_hit: false,
      });

    renderWithClient(<ProfileView handle="alice" />);

    // Wait for the profile body to land.
    await waitFor(() =>
      expect(screen.getByText(/Alice Liddell/)).toBeInTheDocument(),
    );

    // Error surface lands on the failed query.
    const errorBlock = await waitFor(() =>
      screen.getByTestId("recommendation-strip-error"),
    );
    expect(errorBlock).toHaveTextContent(/recommendations couldn’t load/);
    // No URL or status code in the DOM.
    expect(errorBlock.textContent ?? "").not.toMatch(/http/i);
    expect(errorBlock.textContent ?? "").not.toMatch(/503/);
    expect(errorBlock.textContent ?? "").not.toMatch(/timeout/);

    // Two calls so far (initial + one in-flight retry from React Query).
    expect(getMyRecommendations).toHaveBeenCalledTimes(2);

    // Click retry → refetch fires.
    const retryBtn = screen.getByTestId("recommendation-strip-retry");
    fireEvent.click(retryBtn);
    await waitFor(() =>
      expect(getMyRecommendations.mock.calls.length).toBeGreaterThan(2),
    );
  });

  it("does not render the retry surface for anonymous viewers", async () => {
    getProfile.mockResolvedValue(FIXTURE);
    const { ApiError } = await import("@/lib/api");
    me.mockRejectedValue(new ApiError("unauthorized", 401, null));

    renderWithClient(<ProfileView handle="alice" />);

    await waitFor(() =>
      expect(screen.getByText(/Alice Liddell/)).toBeInTheDocument(),
    );

    expect(screen.queryByTestId("recommendation-strip-error")).toBeNull();
    expect(screen.queryByTestId("recommendation-strip-retry")).toBeNull();
    // The recommendations endpoint is never called for the anonymous
    // path (``enabled: isOwner`` gates it).
    expect(getMyRecommendations).not.toHaveBeenCalled();
  });

  it("does not render the retry surface for other-user viewers", async () => {
    getProfile.mockResolvedValue(FIXTURE);
    me.mockResolvedValue({
      id: "22222222-2222-2222-2222-222222222222",
      email: "bob@example.com",
      handle: "bob",
      display_name: "Bob",
      csrf_token: "csrf-token",
      created_at: "2026-01-12T00:00:00Z",
      tutorial_replay_count: 0,
    });

    renderWithClient(<ProfileView handle="alice" />);

    await waitFor(() =>
      expect(screen.getByText(/Alice Liddell/)).toBeInTheDocument(),
    );

    expect(screen.queryByTestId("recommendation-strip-error")).toBeNull();
    expect(getMyRecommendations).not.toHaveBeenCalled();
  });
});
