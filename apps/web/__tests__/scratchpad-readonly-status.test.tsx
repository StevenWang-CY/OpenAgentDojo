/**
 * P1 — ScratchpadPane read-only branch regression test.
 *
 * WorkspaceShell previously gated ``<ScratchpadPane />`` on
 * ``status === "active"``, which unmounted the pane the moment a session
 * graded / gave_up / went into any terminal state. The pane's own
 * read-only branch (banner + disabled textarea) was unreachable as a
 * result. This test mounts the pane directly with a terminal status and
 * asserts the read-only surface renders so the user can still review
 * the notes they took during the session.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { API_BASE, server } from "./contract/_setup";
import { ScratchpadPane } from "@/components/workspace/ScratchpadPane";
import { useWorkspaceStore } from "@/stores/workspaceStore";

const SESSION_ID = "22222222-3333-4444-5555-666666666666";

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
    message: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock("@/lib/telemetry", () => ({
  track: vi.fn(),
}));

beforeEach(() => {
  localStorage.clear();
  useWorkspaceStore(SESSION_ID).getState().reset();
  useWorkspaceStore(SESSION_ID).getState().setScratchpadOpen(true);
  server.use(
    http.get(
      `${API_BASE}/api/v1/sessions/${SESSION_ID}/note`,
      () =>
        HttpResponse.json(
          { body: "earlier notes", updated_at: new Date().toISOString() },
          { status: 200 },
        ),
    ),
  );
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  server.resetHandlers();
  server.close();
});

describe("ScratchpadPane — read-only branch on terminal status (P1)", () => {
  it.each(["graded", "gave_up", "abandoned", "error", "submitting"])(
    "renders the pane with the read-only banner when status is %s",
    async (status) => {
      render(<ScratchpadPane sessionId={SESSION_ID} sessionStatus={status} />);

      // The pane mounts (not null-rendered) even though the session is
      // terminal — the previous gate would have unmounted it entirely.
      expect(await screen.findByTestId("scratchpad-pane")).toBeInTheDocument();

      // Banner explains why typing won't land.
      expect(
        await screen.findByTestId("scratchpad-inactive-banner"),
      ).toBeInTheDocument();

      // Textarea is present but disabled so the user can still scroll
      // through the saved body.
      const textarea = (await screen.findByTestId(
        "scratchpad-textarea",
      )) as HTMLTextAreaElement;
      await waitFor(() => expect(textarea.value).toBe("earlier notes"));
      expect(textarea).toBeDisabled();
    },
  );
});
