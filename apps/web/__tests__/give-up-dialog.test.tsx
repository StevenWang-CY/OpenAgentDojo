/**
 * P0-4 — GiveUpDialog component tests.
 *
 * Asserts the FE contract:
 *  - The trigger button is disabled before the 10-min gate elapses.
 *  - The label includes a countdown when disabled.
 *  - After the gate the button becomes clickable; confirming calls
 *    ``api.giveUpSession`` and routes to the report page.
 *  - 425 errors render a toast with the seconds_remaining wait.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { GiveUpDialog } from "@/components/workspace/GiveUpDialog";
import { API_BASE, server } from "./contract/_setup";

const sessionId = "11111111-1111-1111-1111-111111111111";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/workspace/" + sessionId,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
const toastMessage = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    error: (...args: unknown[]) => toastError(...args),
    success: (...args: unknown[]) => toastSuccess(...args),
    message: (...args: unknown[]) => toastMessage(...args),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

const trackMock = vi.fn();
vi.mock("@/lib/telemetry", () => ({
  track: (...args: unknown[]) => trackMock(...args),
}));

beforeEach(() => {
  pushMock.mockClear();
  toastError.mockClear();
  toastSuccess.mockClear();
  toastMessage.mockClear();
  trackMock.mockClear();
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  server.resetHandlers();
  server.close();
});

describe("GiveUpDialog", () => {
  it("disables the trigger before 10 minutes elapse and shows a countdown", () => {
    // Started 30 seconds ago — gate has ~9m30s remaining.
    const startedAt = new Date(Date.now() - 30_000).toISOString();

    render(
      <GiveUpDialog
        sessionId={sessionId}
        sessionStartedAt={startedAt}
      />,
    );

    const trigger = screen.getByTestId("give-up-trigger") as HTMLButtonElement;
    expect(trigger.disabled).toBe(true);
    // Countdown label should appear in some form (e.g. "9m30" or "9m29").
    expect(trigger.textContent).toMatch(/Give up · 9m/);
  });

  it("enables the trigger and posts to /give-up after the gate elapses", async () => {
    // Started 11 minutes ago — gate has already opened.
    const startedAt = new Date(Date.now() - 11 * 60_000).toISOString();

    server.use(
      http.post(`${API_BASE}/api/v1/sessions/:id/give-up`, () =>
        HttpResponse.json(
          {
            id: "deadbeef-1234-5678-9abc-aaaaaaaaaaaa",
            session_id: sessionId,
            final_diff: "",
            total_score: 50,
            visible_test_results: [],
            hidden_test_results: [],
            validator_results: [],
            score_report: {
              total: 50,
              score_cap_reason: "gave_up",
              uncapped_total: 78,
              dimensions: {},
              strengths: [],
              weaknesses: [],
              missed_failure_mode: false,
              badges_earned: [],
            },
            created_at: "2026-05-23T10:11:00Z",
            ideal_solution: null,
            ideal_solution_diff: null,
            agent_patch_diff: null,
            critical_moments: [],
            score_cap_reason: "gave_up",
            mission_id: "auth-cookie-expiration",
          },
          { status: 200 },
        ),
      ),
    );

    render(
      <GiveUpDialog
        sessionId={sessionId}
        sessionStartedAt={startedAt}
      />,
    );

    const trigger = screen.getByTestId("give-up-trigger") as HTMLButtonElement;
    expect(trigger.disabled).toBe(false);
    expect(trigger.textContent).toMatch(/Give up & reveal/);

    fireEvent.click(trigger);
    const confirm = await screen.findByTestId("give-up-confirm");
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(pushMock).toHaveBeenCalledWith(
        "/report/deadbeef-1234-5678-9abc-aaaaaaaaaaaa",
      ),
    );
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("surfaces 425 errors as a toast with the seconds_remaining wait", async () => {
    const startedAt = new Date(Date.now() - 11 * 60_000).toISOString();

    server.use(
      http.post(`${API_BASE}/api/v1/sessions/:id/give-up`, () =>
        HttpResponse.json(
          {
            detail: {
              code: "give_up_not_yet_available",
              message: "not yet",
              seconds_remaining: 95,
              seconds_required: 600,
            },
          },
          { status: 425 },
        ),
      ),
    );

    render(
      <GiveUpDialog
        sessionId={sessionId}
        sessionStartedAt={startedAt}
      />,
    );

    fireEvent.click(screen.getByTestId("give-up-trigger"));
    const confirm = await screen.findByTestId("give-up-confirm");
    fireEvent.click(confirm);

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    const errMsg = toastError.mock.calls[0]?.[0] as string;
    expect(errMsg).toMatch(/1m 35s|95s/);
  });

  it("only fires telemetry on the success path (audit fix)", async () => {
    // Confirm a 425 rejection does NOT increment session_gave_up — the
    // counter should reflect actual abandons, not server-rejected attempts.
    const startedAt = new Date(Date.now() - 11 * 60_000).toISOString();
    server.use(
      http.post(`${API_BASE}/api/v1/sessions/:id/give-up`, () =>
        HttpResponse.json(
          { detail: { code: "give_up_not_yet_available", seconds_remaining: 30 } },
          { status: 425 },
        ),
      ),
    );

    render(
      <GiveUpDialog
        sessionId={sessionId}
        sessionStartedAt={startedAt}
      />,
    );
    fireEvent.click(screen.getByTestId("give-up-trigger"));
    fireEvent.click(await screen.findByTestId("give-up-confirm"));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(trackMock).not.toHaveBeenCalled();
  });

  it("renders a distinct toast for tutorial give-up rejection (409)", async () => {
    const startedAt = new Date(Date.now() - 11 * 60_000).toISOString();
    server.use(
      http.post(`${API_BASE}/api/v1/sessions/:id/give-up`, () =>
        HttpResponse.json(
          {
            detail: {
              code: "give_up_not_supported_for_tutorial",
              message: "tutorial",
              session_status: "active",
            },
          },
          { status: 409 },
        ),
      ),
    );

    render(
      <GiveUpDialog
        sessionId={sessionId}
        sessionStartedAt={startedAt}
      />,
    );
    fireEvent.click(screen.getByTestId("give-up-trigger"));
    fireEvent.click(await screen.findByTestId("give-up-confirm"));
    await waitFor(() => expect(toastMessage).toHaveBeenCalled());
    const msg = toastMessage.mock.calls[0]?.[0] as string;
    expect(msg).toMatch(/tutorial|orientation/i);
    expect(toastError).not.toHaveBeenCalled();
    expect(trackMock).not.toHaveBeenCalled();
  });

  it("keeps the dialog open on error so the user can read the toast (audit fix)", async () => {
    const startedAt = new Date(Date.now() - 11 * 60_000).toISOString();
    server.use(
      http.post(`${API_BASE}/api/v1/sessions/:id/give-up`, () =>
        HttpResponse.json(
          { detail: { code: "session_not_active", session_status: "submitting" } },
          { status: 409 },
        ),
      ),
    );

    render(
      <GiveUpDialog
        sessionId={sessionId}
        sessionStartedAt={startedAt}
      />,
    );
    fireEvent.click(screen.getByTestId("give-up-trigger"));
    fireEvent.click(await screen.findByTestId("give-up-confirm"));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    // The dialog content should still be in the DOM after the error.
    expect(screen.queryByTestId("give-up-dialog")).not.toBeNull();
  });

  it("keeps the gate closed when sessionStartedAt is malformed (audit fix)", () => {
    render(
      <GiveUpDialog
        sessionId={sessionId}
        sessionStartedAt="not-a-real-date"
      />,
    );
    const trigger = screen.getByTestId("give-up-trigger") as HTMLButtonElement;
    expect(trigger.disabled).toBe(true);
  });
});
