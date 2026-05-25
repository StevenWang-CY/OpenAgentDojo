/**
 * P0-9 — CommandPalette tests.
 *
 * Asserts the FE contract:
 *  - Hitting Cmd+P opens the palette (in workspace context).
 *  - Typing into the input fetches getFileList with the debounced query.
 *  - Pressing Enter on the highlighted row fires onSelect.
 *  - The "truncated" pill appears when the API marks the listing truncated.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { CommandPalette } from "@/components/workspace/CommandPalette";
import { API_BASE, server } from "./contract/_setup";

const sessionId = "11111111-1111-1111-1111-111111111111";

beforeEach(() => {
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  server.resetHandlers();
  server.close();
});

describe("CommandPalette", () => {
  it("fetches file list on open and selects a row on Enter", async () => {
    server.use(
      http.get(
        `${API_BASE}/api/v1/sessions/${sessionId}/files/list`,
        () =>
          HttpResponse.json(
            {
              paths: ["README.md", "src/auth.ts", "src/index.ts"],
              truncated: false,
              total: 3,
            },
            { status: 200 },
          ),
      ),
    );

    const onSelect = vi.fn();
    const onOpenChange = vi.fn();

    render(
      <CommandPalette
        sessionId={sessionId}
        open={true}
        onOpenChange={onOpenChange}
        onSelect={onSelect}
      />,
    );

    // The input is auto-focused on open.
    const input = await screen.findByTestId("command-palette-input");
    expect(input).toBeInTheDocument();

    // Rows arrive after the debounced fetch completes.
    await waitFor(async () => {
      const rows = await screen.findAllByTestId("command-palette-row");
      expect(rows.length).toBe(3);
    });

    // Pressing Enter picks the first (highlighted) row.
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith("README.md");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("renders the truncation chip when the API marks results truncated", async () => {
    server.use(
      http.get(
        `${API_BASE}/api/v1/sessions/${sessionId}/files/list`,
        () =>
          HttpResponse.json(
            {
              paths: ["a.ts", "b.ts"],
              truncated: true,
              total: 50,
            },
            { status: 200 },
          ),
      ),
    );

    render(
      <CommandPalette
        sessionId={sessionId}
        open={true}
        onOpenChange={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    await waitFor(async () => {
      const chip = await screen.findByTestId("command-palette-truncated");
      expect(chip).toBeInTheDocument();
    });
  });

  it("re-issues the search with the debounced query", async () => {
    let lastQuery: string | null = null;
    server.use(
      http.get(
        `${API_BASE}/api/v1/sessions/${sessionId}/files/list`,
        ({ request }) => {
          const url = new URL(request.url);
          lastQuery = url.searchParams.get("query");
          return HttpResponse.json(
            {
              paths: lastQuery === "auth" ? ["src/auth.ts"] : [],
              truncated: false,
              total: lastQuery === "auth" ? 1 : 0,
            },
            { status: 200 },
          );
        },
      ),
    );

    render(
      <CommandPalette
        sessionId={sessionId}
        open={true}
        onOpenChange={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    const input = await screen.findByTestId("command-palette-input");
    await act(async () => {
      fireEvent.change(input, { target: { value: "auth" } });
    });

    await waitFor(() => {
      expect(lastQuery).toBe("auth");
    });
    await waitFor(async () => {
      const rows = await screen.findAllByTestId("command-palette-row");
      expect(rows.length).toBe(1);
    });
  });
});
