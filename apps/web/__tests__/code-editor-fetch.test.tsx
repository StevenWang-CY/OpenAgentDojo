import * as React from "react";
import { render, waitFor } from "@testing-library/react";
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
});
