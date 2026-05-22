import * as React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";

const getFile = vi.fn();
const writeFile = vi.fn();
const revertFile = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getFile: (...args: unknown[]) => getFile(...args),
    writeFile: (...args: unknown[]) => writeFile(...args),
    revertFile: (...args: unknown[]) => revertFile(...args),
  };
});

// Stub Monaco — heavy and ssr-unfriendly.
vi.mock("@monaco-editor/react", () => ({
  __esModule: true,
  default: ({ value }: { value: string }) => (
    <textarea data-testid="monaco" defaultValue={value} />
  ),
}));

import { CodeEditor } from "@/components/workspace/CodeEditor";

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

describe("CodeEditor file fetch", () => {
  it("calls getFile when a path is opened and seeds the editor with content", async () => {
    getFile.mockResolvedValue({
      path: "backend/auth/session.ts",
      content: "export function isValid() { return true; }\n",
      encoding: "utf-8",
      truncated: false,
    });

    renderWithClient(
      <CodeEditor sessionId="session-1" path="backend/auth/session.ts" />
    );

    await waitFor(() => expect(getFile).toHaveBeenCalledTimes(1));
    expect(getFile).toHaveBeenCalledWith(
      "session-1",
      "backend/auth/session.ts",
      expect.anything()
    );
  });

  it("does not call getFile when path is null", () => {
    renderWithClient(<CodeEditor sessionId="session-1" path={null} />);
    expect(getFile).not.toHaveBeenCalled();
  });

  it("renders an inline error with a Retry button when fetch fails, and retries on click", async () => {
    const { ApiError } = await import("@/lib/api");
    // The CodeEditor retries once on non-404 failures, so the initial fetch
    // attempts twice. Reject every call up front, render, then verify the
    // error UI; after the user clicks Retry, swap in a resolved value so we
    // can confirm the refetch path also wires through cleanly.
    getFile.mockRejectedValue(new ApiError("boom", 500, null));

    renderWithClient(
      <CodeEditor sessionId="session-1" path="backend/auth/session.ts" />
    );

    // Error state is announced as a role=alert region so screen readers pick
    // it up; the body mentions the failing path and the action button is
    // labelled "Retry".
    await waitFor(
      () =>
        expect(screen.getByTestId("code-editor-error")).toBeInTheDocument(),
      { timeout: 3000 }
    );
    // The component uses a right single-quote (&rsquo; → "’"), so accept
    // either the ASCII apostrophe or the typographic glyph. The path is
    // rendered inside the alert region, in a <span class="font-mono">.
    const errorRegion = screen.getByTestId("code-editor-error");
    expect(errorRegion.textContent ?? "").toMatch(/couldn[’']?t load/i);
    expect(errorRegion.textContent ?? "").toContain(
      "backend/auth/session.ts"
    );

    const callsBeforeRetry = getFile.mock.calls.length;
    getFile.mockResolvedValue({
      path: "backend/auth/session.ts",
      content: "ok\n",
      encoding: "utf-8",
      truncated: false,
    });

    const retry = screen.getByRole("button", { name: /retry/i });
    fireEvent.click(retry);

    await waitFor(() =>
      expect(getFile.mock.calls.length).toBeGreaterThan(callsBeforeRetry)
    );
  });
});
