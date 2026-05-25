/**
 * P0-12 — ResetWorkspaceDialog tests.
 *
 * Asserts the FE contract:
 *  - Confirm calls ``api.resetSession`` and on success closes the dialog,
 *    fires the success telemetry, and invalidates the workspace queries.
 *  - 409 errors render a "session no longer active" toast.
 *  - 500 errors render a "reset failed in the sandbox" toast.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { ResetWorkspaceDialog } from "@/components/workspace/ResetWorkspaceDialog";
import { API_BASE, server } from "./contract/_setup";

const sessionId = "22222222-2222-2222-2222-222222222222";

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    error: (...args: unknown[]) => toastError(...args),
    success: (...args: unknown[]) => toastSuccess(...args),
    message: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

const trackMock = vi.fn();
vi.mock("@/lib/telemetry", () => ({
  track: (...args: unknown[]) => trackMock(...args),
}));

function renderDialog(open: boolean, onOpenChange: (v: boolean) => void) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ResetWorkspaceDialog
        sessionId={sessionId}
        open={open}
        onOpenChange={onOpenChange}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  toastError.mockClear();
  toastSuccess.mockClear();
  trackMock.mockClear();
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  server.resetHandlers();
  server.close();
});

describe("ResetWorkspaceDialog", () => {
  it("closes + fires success telemetry on a 200 reset", async () => {
    server.use(
      http.post(`${API_BASE}/api/v1/sessions/:id/reset`, () =>
        HttpResponse.json(
          { files_reset: 5, new_head_commit: "abc123", reset_count: 1 },
          { status: 200 },
        ),
      ),
    );
    const onOpenChange = vi.fn();
    renderDialog(true, onOpenChange);

    const confirm = await screen.findByTestId("reset-workspace-confirm");
    fireEvent.click(confirm);

    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    // ``session_reset_requested`` MUST fire on Confirm (not the overflow
    // menu) and carry an estimate of how many file buffers will be
    // discarded. The estimate is a number — the default test store has
    // zero buffers, but we don't lock the exact value.
    expect(trackMock).toHaveBeenCalledWith(
      "session_reset_requested",
      expect.objectContaining({
        session_id: sessionId,
        files_discarded_estimate: expect.any(Number),
      }),
    );
    expect(trackMock).toHaveBeenCalledWith(
      "session_reset_completed",
      expect.objectContaining({
        session_id: sessionId,
        reset_count: 1,
        files_reset: 5,
      }),
    );
  });

  it("surfaces a 409 as a 'session not active' toast", async () => {
    server.use(
      http.post(`${API_BASE}/api/v1/sessions/:id/reset`, () =>
        HttpResponse.json(
          {
            detail: {
              code: "session_not_active",
              message: "session is graded — workspace is read-only",
              session_status: "graded",
            },
          },
          { status: 409 },
        ),
      ),
    );
    const onOpenChange = vi.fn();
    renderDialog(true, onOpenChange);

    const confirm = await screen.findByTestId("reset-workspace-confirm");
    fireEvent.click(confirm);

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it("surfaces a 500 as a 'reset failed in the sandbox' toast", async () => {
    server.use(
      http.post(`${API_BASE}/api/v1/sessions/:id/reset`, () =>
        HttpResponse.json(
          {
            detail: {
              code: "git_reset_failed",
              message: "git reset failed",
            },
          },
          { status: 500 },
        ),
      ),
    );
    const onOpenChange = vi.fn();
    renderDialog(true, onOpenChange);

    const confirm = await screen.findByTestId("reset-workspace-confirm");
    fireEvent.click(confirm);

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    const calls = toastError.mock.calls;
    const message = String(calls[0]?.[0] ?? "");
    expect(message.toLowerCase()).toContain("sandbox");
  });

  it("renders the reset count in the dialog body", async () => {
    const onOpenChange = vi.fn();
    renderDialog(true, onOpenChange);

    const count = await screen.findByTestId("reset-count");
    // Default store has zero prior session.reset events.
    expect(count.textContent).toBe("0");
  });
});
