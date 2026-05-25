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

  it("ignores the stale invalidation/refetch from an earlier revert when a newer revert fires mid-flight", async () => {
    // FE-P2 audit fix — handleRevert captures a monotonic `revertEpoch`
    // before awaiting `revertFile`. If a second revert starts while the
    // first is mid-await, the first one's post-await invalidate/refetch
    // chain must bail (the newer one will own the final write into the
    // store). Otherwise we'd flicker pre-revert content over the
    // freshly-reverted file.
    getFile.mockResolvedValue({
      path: "backend/auth/session.ts",
      content: "first\n",
      encoding: "utf-8",
      truncated: false,
    });

    // Resolve revertFile slowly enough that we can fire a second revert
    // before the first's promise settles.
    let resolveFirstRevert!: () => void;
    const firstRevertPending = new Promise<void>((res) => {
      resolveFirstRevert = res;
    });
    revertFile
      .mockImplementationOnce(async () => {
        await firstRevertPending;
      })
      .mockImplementationOnce(async () => {
        // Second revert resolves immediately.
      });

    renderWithClient(
      <CodeEditor sessionId="session-1" path="backend/auth/session.ts" />,
    );

    await waitFor(() => expect(getFile).toHaveBeenCalledTimes(1));
    const callsAfterMount = getFile.mock.calls.length;

    const revertBtn = await screen.findByRole("button", {
      name: /revert backend\/auth\/session\.ts/i,
    });

    // Two reverts in quick succession.
    fireEvent.click(revertBtn);
    fireEvent.click(revertBtn);

    // Both reverts are now in flight; only the second one's epoch matches.
    // Allow the second revert (and its post-await invalidate/fetch) to run.
    await waitFor(() => expect(revertFile).toHaveBeenCalledTimes(2));

    // The second revert refetches the file exactly once.
    await waitFor(() =>
      expect(getFile.mock.calls.length).toBe(callsAfterMount + 1),
    );

    const callsAfterSecondRevert = getFile.mock.calls.length;

    // Now finish the first (stale) revert. Its post-await invalidate/fetch
    // chain must bail out because the epoch has moved on — so getFile must
    // NOT be called a second time after this.
    resolveFirstRevert();

    // Give the microtask queue a tick to settle the stale revert.
    await new Promise((r) => setTimeout(r, 10));
    expect(getFile.mock.calls.length).toBe(callsAfterSecondRevert);
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
