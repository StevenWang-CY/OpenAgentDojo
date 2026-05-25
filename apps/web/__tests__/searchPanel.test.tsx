/**
 * P0-9 — SearchPanel tests.
 *
 * Asserts the FE contract:
 *  - Typing a query fires POST /files/search after the debounce.
 *  - The regex toggle reaches the request body unchanged.
 *  - Clicking a match invokes onSelect(path, line).
 *  - An invalid_regex error body renders a typed message.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { SearchPanel } from "@/components/workspace/SearchPanel";
import { API_BASE, server } from "./contract/_setup";

const sessionId = "33333333-3333-3333-3333-333333333333";

beforeEach(() => {
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  server.resetHandlers();
  server.close();
});

describe("SearchPanel", () => {
  it("fetches matches and renders them grouped by file", async () => {
    server.use(
      http.post(
        `${API_BASE}/api/v1/sessions/${sessionId}/files/search`,
        () =>
          HttpResponse.json(
            {
              matches: [
                {
                  path: "src/auth.ts",
                  line_number: 4,
                  line_text: "const SECRET = 'abc';",
                  match_start: 6,
                  match_end: 12,
                },
                {
                  path: "src/auth.ts",
                  line_number: 9,
                  line_text: "return SECRET;",
                  match_start: 7,
                  match_end: 13,
                },
              ],
              truncated: false,
              total: 2,
              duration_ms: 17,
            },
            { status: 200 },
          ),
      ),
    );

    render(
      <SearchPanel
        sessionId={sessionId}
        open={true}
        onClose={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    const input = await screen.findByTestId("search-panel-input");
    await act(async () => {
      fireEvent.change(input, { target: { value: "SECRET" } });
    });

    await waitFor(async () => {
      const matches = await screen.findAllByTestId("search-panel-match");
      expect(matches.length).toBe(2);
    });

    // Footer reports the count + duration.
    const footer = await screen.findByTestId("search-panel-footer");
    expect(footer.textContent).toMatch(/2 matches/);
    expect(footer.textContent).toMatch(/17ms/);
  });

  it("propagates the regex toggle to the request body", async () => {
    let lastBody: unknown = null;
    server.use(
      http.post(
        `${API_BASE}/api/v1/sessions/${sessionId}/files/search`,
        async ({ request }) => {
          lastBody = await request.json();
          return HttpResponse.json(
            { matches: [], truncated: false, total: 0, duration_ms: 1 },
            { status: 200 },
          );
        },
      ),
    );

    render(
      <SearchPanel
        sessionId={sessionId}
        open={true}
        onClose={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    // Toggle regex on first.
    const regexToggle = await screen.findByTestId("search-panel-regex");
    fireEvent.click(regexToggle);
    const input = await screen.findByTestId("search-panel-input");
    await act(async () => {
      fireEvent.change(input, { target: { value: "foo|bar" } });
    });

    await waitFor(() => {
      expect(lastBody).not.toBeNull();
    });
    expect(lastBody).toMatchObject({ query: "foo|bar", regex: true });
  });

  it("invokes onSelect(path, line) when a match is clicked", async () => {
    server.use(
      http.post(
        `${API_BASE}/api/v1/sessions/${sessionId}/files/search`,
        () =>
          HttpResponse.json(
            {
              matches: [
                {
                  path: "src/index.ts",
                  line_number: 42,
                  line_text: "console.log('hi');",
                  match_start: 8,
                  match_end: 11,
                },
              ],
              truncated: false,
              total: 1,
              duration_ms: 3,
            },
            { status: 200 },
          ),
      ),
    );
    const onSelect = vi.fn();
    render(
      <SearchPanel
        sessionId={sessionId}
        open={true}
        onClose={vi.fn()}
        onSelect={onSelect}
      />,
    );
    const input = await screen.findByTestId("search-panel-input");
    await act(async () => {
      fireEvent.change(input, { target: { value: "log" } });
    });
    const match = await screen.findByTestId("search-panel-match");
    fireEvent.click(match);
    expect(onSelect).toHaveBeenCalledWith("src/index.ts", 42);
  });

  it("renders the typed invalid_regex message on a 400", async () => {
    server.use(
      http.post(
        `${API_BASE}/api/v1/sessions/${sessionId}/files/search`,
        () =>
          HttpResponse.json(
            {
              detail: {
                code: "invalid_regex",
                message: "regex parse error at position 1",
              },
            },
            { status: 400 },
          ),
      ),
    );

    render(
      <SearchPanel
        sessionId={sessionId}
        open={true}
        onClose={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    const regexToggle = await screen.findByTestId("search-panel-regex");
    fireEvent.click(regexToggle);
    const input = await screen.findByTestId("search-panel-input");
    await act(async () => {
      fireEvent.change(input, { target: { value: "(" } });
    });
    const err = await screen.findByTestId("search-panel-error");
    expect(err.textContent).toMatch(/invalid regex/i);
  });
});
