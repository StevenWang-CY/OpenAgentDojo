/**
 * P1-4 — ScratchpadPane component tests.
 *
 * Asserts the FE contract for the bottom-of-AgentChat notes pane:
 *  - Empty initial GET → textarea renders empty + enabled.
 *  - Typing triggers an autosave PUT after the 1.5 s debounce.
 *  - Input is clamped to 32 KiB on the way in AND a toast fires when the
 *    cap is reached.
 *  - A 413 response from the server also surfaces the toast.
 *  - A 409 response disables input and renders the read-only banner.
 *  - Toggling the header writes ``scratchpadOpen`` to the store (persisted
 *    via the persist middleware).
 *  - ``track('scratchpad_opened')`` fires the first time the user expands
 *    the pane.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { API_BASE, server } from "./contract/_setup";
import { ScratchpadPane, SCRATCHPAD_LIMIT_BYTES } from "@/components/workspace/ScratchpadPane";
import { useWorkspaceStore } from "@/stores/workspaceStore";

const SESSION_ID = "11111111-2222-3333-4444-555555555555";

// Sonner is mocked so the tests can assert toast.error without rendering
// the live <Toaster /> (which would noise up the DOM with portal nodes).
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
  toastError.mockClear();
  toastSuccess.mockClear();
  toastMessage.mockClear();
  trackMock.mockClear();
  // Reset the per-session store so persistence from a prior test (default
  // pane open/closed, byte count) doesn't bleed in.
  localStorage.clear();
  useWorkspaceStore(SESSION_ID).getState().reset();
  useWorkspaceStore(SESSION_ID).getState().setScratchpadOpen(false);
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  server.resetHandlers();
  server.close();
  vi.useRealTimers();
});

function mockEmptyGet(): void {
  server.use(
    http.get(
      `${API_BASE}/api/v1/sessions/${SESSION_ID}/note`,
      () =>
        HttpResponse.json(
          { body: "", updated_at: new Date().toISOString() },
          { status: 200 },
        ),
    ),
  );
}

describe("ScratchpadPane", () => {
  it("renders empty when the initial GET returns an empty body", async () => {
    mockEmptyGet();
    // Open the pane by default so the textarea actually mounts.
    useWorkspaceStore(SESSION_ID).getState().setScratchpadOpen(true);

    render(<ScratchpadPane sessionId={SESSION_ID} sessionStatus="active" />);

    const textarea = (await screen.findByTestId(
      "scratchpad-textarea",
    )) as HTMLTextAreaElement;
    await waitFor(() => expect(textarea.placeholder).toMatch(/jot reasoning/));
    expect(textarea.value).toBe("");
    expect(textarea).not.toBeDisabled();
  });

  it("debounces autosave to a single PUT 1.5 s after the last keystroke", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockEmptyGet();
    // Record the bodies inline so we don't rely on cloning the (consumed)
    // request after the handler resolves.
    const seenBodies: string[] = [];
    server.use(
      http.put(
        `${API_BASE}/api/v1/sessions/${SESSION_ID}/note`,
        async ({ request }) => {
          const payload = (await request.json()) as { body: string };
          seenBodies.push(payload.body);
          return HttpResponse.json(
            { body: payload.body, updated_at: new Date().toISOString() },
            { status: 200 },
          );
        },
      ),
    );
    useWorkspaceStore(SESSION_ID).getState().setScratchpadOpen(true);

    render(<ScratchpadPane sessionId={SESSION_ID} sessionStatus="active" />);

    const textarea = (await screen.findByTestId(
      "scratchpad-textarea",
    )) as HTMLTextAreaElement;
    await waitFor(() => expect(textarea).not.toBeDisabled());

    // Two keystrokes within the 1.5s window — only one PUT should fire.
    fireEvent.change(textarea, { target: { value: "first" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });
    fireEvent.change(textarea, { target: { value: "first and second" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_600);
    });

    await waitFor(() => expect(seenBodies.length).toBe(1));
    expect(seenBodies[0]).toBe("first and second");
  });

  it("clamps input at 32 KiB and surfaces the toast", async () => {
    mockEmptyGet();
    server.use(
      http.put(
        `${API_BASE}/api/v1/sessions/${SESSION_ID}/note`,
        () =>
          HttpResponse.json(
            { body: "", updated_at: new Date().toISOString() },
            { status: 200 },
          ),
      ),
    );
    useWorkspaceStore(SESSION_ID).getState().setScratchpadOpen(true);

    render(<ScratchpadPane sessionId={SESSION_ID} sessionStatus="active" />);
    const textarea = (await screen.findByTestId(
      "scratchpad-textarea",
    )) as HTMLTextAreaElement;
    await waitFor(() => expect(textarea).not.toBeDisabled());

    // 40 KiB of ASCII text — well above the 32 KiB cap.
    const oversize = "x".repeat(40_000);
    fireEvent.change(textarea, { target: { value: oversize } });

    // The textarea value is clamped on the way in.
    await waitFor(() =>
      expect(textarea.value.length).toBeLessThanOrEqual(SCRATCHPAD_LIMIT_BYTES),
    );
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/scratchpad is full/i),
      expect.objectContaining({ id: "scratchpad-full" }),
    );
  });

  it("surfaces the toast when the server returns 413 scratchpad_too_large", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockEmptyGet();
    server.use(
      http.put(
        `${API_BASE}/api/v1/sessions/${SESSION_ID}/note`,
        () =>
          HttpResponse.json(
            {
              detail: "scratchpad_too_large",
              code: "scratchpad_too_large",
              limit_bytes: 32_768,
            },
            { status: 413 },
          ),
      ),
    );
    useWorkspaceStore(SESSION_ID).getState().setScratchpadOpen(true);

    render(<ScratchpadPane sessionId={SESSION_ID} sessionStatus="active" />);
    const textarea = (await screen.findByTestId(
      "scratchpad-textarea",
    )) as HTMLTextAreaElement;
    await waitFor(() => expect(textarea).not.toBeDisabled());

    // A normal-sized edit — the server returns 413 anyway (simulates a
    // server-side cap reduction landing while the FE still thinks it has
    // headroom).
    fireEvent.change(textarea, { target: { value: "ok body" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_600);
    });

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        expect.stringMatching(/scratchpad is full/i),
        expect.objectContaining({ id: "scratchpad-full" }),
      ),
    );
  });

  it("disables input and shows a read-only banner on 409 session_not_active", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockEmptyGet();
    server.use(
      http.put(
        `${API_BASE}/api/v1/sessions/${SESSION_ID}/note`,
        () =>
          HttpResponse.json(
            { detail: "session_not_active", code: "session_not_active" },
            { status: 409 },
          ),
      ),
    );
    useWorkspaceStore(SESSION_ID).getState().setScratchpadOpen(true);

    render(<ScratchpadPane sessionId={SESSION_ID} sessionStatus="active" />);
    const textarea = (await screen.findByTestId(
      "scratchpad-textarea",
    )) as HTMLTextAreaElement;
    await waitFor(() => expect(textarea).not.toBeDisabled());

    fireEvent.change(textarea, { target: { value: "hello" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_600);
    });

    // Banner appears and the textarea flips to disabled.
    expect(
      await screen.findByTestId("scratchpad-inactive-banner"),
    ).toBeInTheDocument();
    await waitFor(() => expect(textarea).toBeDisabled());
  });

  it("toggles the pane open and persists scratchpadOpen to the store", async () => {
    mockEmptyGet();
    render(<ScratchpadPane sessionId={SESSION_ID} sessionStatus="active" />);

    // The pane starts collapsed (default false). Toggle header to expand.
    const toggle = await screen.findByTestId("scratchpad-toggle");
    expect(useWorkspaceStore(SESSION_ID).getState().scratchpadOpen).toBe(false);

    fireEvent.click(toggle);
    await waitFor(() =>
      expect(useWorkspaceStore(SESSION_ID).getState().scratchpadOpen).toBe(true),
    );
    expect(screen.getByTestId("scratchpad-textarea")).toBeInTheDocument();

    // Toggle again to collapse.
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(useWorkspaceStore(SESSION_ID).getState().scratchpadOpen).toBe(false),
    );
    expect(screen.queryByTestId("scratchpad-textarea")).not.toBeInTheDocument();
  });

  it("fires scratchpad_opened telemetry on the false→true transition", async () => {
    mockEmptyGet();
    render(<ScratchpadPane sessionId={SESSION_ID} sessionStatus="active" />);

    const toggle = await screen.findByTestId("scratchpad-toggle");
    // Wait for the initial GET so the open transition is the only thing
    // that could fire the event (the mount itself is collapsed, so nothing
    // should have fired yet).
    await waitFor(() =>
      expect(useWorkspaceStore(SESSION_ID).getState().scratchpadOpen).toBe(false),
    );
    expect(trackMock).not.toHaveBeenCalledWith(
      "scratchpad_opened",
      expect.anything(),
    );

    fireEvent.click(toggle);
    await waitFor(() =>
      expect(trackMock).toHaveBeenCalledWith(
        "scratchpad_opened",
        expect.objectContaining({
          session_id: SESSION_ID,
          trigger: "button",
        }),
      ),
    );
  });
});
