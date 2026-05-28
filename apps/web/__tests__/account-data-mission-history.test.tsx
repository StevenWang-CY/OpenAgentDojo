/**
 * Wave 2C — per-row Replay button on the account Data tab.
 *
 * The AccountDataMissionHistory component renders the user's last 25
 * graded sessions and surfaces a per-row Replay button when the row
 * carries a non-null ``submission_id``. These tests pin the contract:
 *
 *   - Replay button renders when submission_id is non-null.
 *   - Replay button is omitted (replaced by an em-dash placeholder)
 *     when submission_id is null.
 *   - Clicking the button calls downloadReplayZip with the submission id
 *     and fires the replay_export_requested telemetry event.
 */
import * as React from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import type { MissionHistoryItem, PublicProfile } from "@arena/shared-types";
import { API_BASE, server } from "./contract/_setup";

// ── Mocks ──────────────────────────────────────────────────────────────────

const downloadReplayZip = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    downloadReplayZip: (...args: unknown[]) => downloadReplayZip(...args),
  };
});

const trackMock = vi.fn();
vi.mock("@/lib/telemetry", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/telemetry")>("@/lib/telemetry");
  return {
    ...actual,
    track: (...args: unknown[]) => trackMock(...args),
  };
});

vi.mock("sonner", () => ({
  toast: {
    promise: vi.fn((p: Promise<unknown>) => p),
    error: vi.fn(),
    success: vi.fn(),
  },
}));

import { AccountDataMissionHistory } from "@/components/account/AccountDataMissionHistory";

const HANDLE = "ada";

function buildProfile(history: MissionHistoryItem[]): PublicProfile {
  return {
    handle: HANDLE,
    display_name: "Ada",
    joined_at: "2026-01-01T00:00:00Z",
    github_login: null,
    github_avatar_url: null,
    github_html_url: null,
    github_verified_at: null,
    badges: [],
    history,
    radar_averages: {},
    has_verified_attempts: false,
    verified_attempts_only: false,
    dimension_trends: {},
    total_missions: history.length,
    best_score: history[0]?.score ?? null,
  };
}

function renderWith(history: MissionHistoryItem[]) {
  server.use(
    http.get(`${API_BASE}/api/v1/profiles/${HANDLE}`, () =>
      HttpResponse.json(buildProfile(history)),
    ),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AccountDataMissionHistory handle={HANDLE} />
    </QueryClientProvider>,
  );
}

const ROW_WITH_SUBMISSION: MissionHistoryItem = {
  session_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  submission_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  mission_id: "auth-cookie-expiration",
  mission_title: "Auth Cookie Expiration",
  completed_at: "2026-05-01T12:00:00Z",
  score: 92,
  difficulty: "intermediate",
};

const ROW_WITHOUT_SUBMISSION: MissionHistoryItem = {
  session_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  submission_id: null,
  mission_id: "api-contract-drift",
  mission_title: "API Contract Drift",
  completed_at: "2026-04-15T09:00:00Z",
  score: 70,
  difficulty: "advanced",
};

beforeEach(() => {
  trackMock.mockClear();
  downloadReplayZip.mockReset();
  server.listen({ onUnhandledRequest: "bypass" });
});

afterEach(() => {
  server.resetHandlers();
  server.close();
});

describe("AccountDataMissionHistory — per-row Replay button", () => {
  it("renders the Replay button when submission_id is non-null", async () => {
    renderWith([ROW_WITH_SUBMISSION]);

    const button = await screen.findByTestId("replay-row-button");
    expect(button).toBeInTheDocument();
    expect(button).toHaveAttribute(
      "data-submission-id",
      ROW_WITH_SUBMISSION.submission_id,
    );
    expect(button).toHaveAccessibleName(/download replay artefact/i);
  });

  it("omits the Replay button when submission_id is null", async () => {
    renderWith([ROW_WITHOUT_SUBMISSION]);

    // Wait for the data to land — once the mission title shows, we know
    // the row rendered and the button decision is final.
    await screen.findByText(ROW_WITHOUT_SUBMISSION.mission_title);
    expect(screen.queryByTestId("replay-row-button")).not.toBeInTheDocument();
  });

  it("clicking the Replay button calls downloadReplayZip with the submission id", async () => {
    downloadReplayZip.mockResolvedValueOnce({
      bytes: 5_120,
      filename: "arena-replay-bbbbbbbb-20260501.zip",
    });
    renderWith([ROW_WITH_SUBMISSION]);

    const button = await screen.findByTestId("replay-row-button");
    await act(async () => {
      fireEvent.click(button);
    });

    await waitFor(() => expect(downloadReplayZip).toHaveBeenCalledTimes(1));
    // The component passes a fresh AbortController per click (Item 20)
    // so the unmount cleanup can cancel an in-flight download.
    expect(downloadReplayZip).toHaveBeenCalledWith(
      ROW_WITH_SUBMISSION.submission_id,
      expect.objectContaining({
        signal: expect.any(AbortSignal),
      }),
    );
    // Telemetry mirrors the ShareDropdown's envelope.
    expect(trackMock).toHaveBeenCalledWith(
      "replay_export_requested",
      expect.objectContaining({
        submission_id: ROW_WITH_SUBMISSION.submission_id,
        kind: "zip",
      }),
    );
  });
});
