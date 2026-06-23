/**
 * P1/P2 — CodeEditor buffer reconciliation.
 *
 *   - A base64 (binary) file is NOT seeded as an editable buffer; instead a
 *     read-only "binary file" state renders so autosave can't write raw
 *     base64 back and corrupt the sandbox file.
 *   - When fresh server content arrives (e.g. a ``patch.applied``-driven
 *     refetch) and the buffer is CLEAN (no unsaved user edits), the clean
 *     buffer is overwritten with the server content. Dirty buffers (real
 *     user edits) are never clobbered.
 */

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

// Stub Monaco — render the live `value` so we can assert what the editor shows,
// and forward `onChange` so we can simulate user typing.
vi.mock("@monaco-editor/react", () => ({
  __esModule: true,
  default: ({
    value,
    onChange,
  }: {
    value: string;
    onChange?: (v: string) => void;
  }) => (
    <textarea
      data-testid="monaco"
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));

import { CodeEditor } from "@/components/workspace/CodeEditor";
import { useWorkspaceStore } from "@/stores/workspaceStore";

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}

function renderWithClient(node: React.ReactElement, client = makeClient()) {
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>{node}</QueryClientProvider>
    ),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  writeFile.mockResolvedValue(undefined);
  // Reset every cached store slice for the session ids used below.
  for (const sid of ["session-bin", "session-clean", "session-dirty"]) {
    useWorkspaceStore(sid).getState().reset();
  }
});

describe("CodeEditor — binary (base64) files", () => {
  it("renders the read-only binary state and never seeds an editable buffer", async () => {
    getFile.mockResolvedValue({
      path: "assets/logo.png",
      content: "iVBORw0KGgoAAAANSUhEUg==", // raw base64 bytes
      encoding: "base64",
      truncated: false,
    });

    renderWithClient(
      <CodeEditor sessionId="session-bin" path="assets/logo.png" />
    );

    await waitFor(() =>
      expect(screen.getByTestId("code-editor-binary")).toBeInTheDocument()
    );
    // No editable Monaco surface, no buffer seeded, no autosave attempted.
    expect(screen.queryByTestId("monaco")).not.toBeInTheDocument();
    expect(
      useWorkspaceStore("session-bin").getState().fileBuffers[
        "assets/logo.png"
      ]
    ).toBeUndefined();
    expect(writeFile).not.toHaveBeenCalled();
    // The Revert affordance is hidden for a binary file.
    expect(
      screen.queryByRole("button", { name: /revert/i })
    ).not.toBeInTheDocument();
  });
});

describe("CodeEditor — clean buffer reconciliation", () => {
  it("overwrites a clean buffer with fresh server content on refetch", async () => {
    getFile.mockResolvedValue({
      path: "src/a.ts",
      content: "OLD",
      encoding: "utf-8",
      truncated: false,
    });

    const { client } = renderWithClient(
      <CodeEditor sessionId="session-clean" path="src/a.ts" />
    );

    // Initial seed.
    await waitFor(() =>
      expect(screen.getByTestId("monaco")).toHaveValue("OLD")
    );

    // Simulate a patch landing: server now returns NEW content, and the
    // ["file", …] query is invalidated (as WorkspaceShell does on
    // patch.applied).
    getFile.mockResolvedValue({
      path: "src/a.ts",
      content: "NEW",
      encoding: "utf-8",
      truncated: false,
    });
    await client.invalidateQueries({
      queryKey: ["file", "session-clean", "src/a.ts"],
    });

    // The clean buffer is replaced with the post-patch server content.
    await waitFor(() =>
      expect(screen.getByTestId("monaco")).toHaveValue("NEW")
    );
  });

  it("never clobbers a dirty buffer (unsaved user edits) on refetch", async () => {
    getFile.mockResolvedValue({
      path: "src/b.ts",
      content: "OLD",
      encoding: "utf-8",
      truncated: false,
    });
    // Keep any (debounced) save pending so a resolved write can't clear the
    // dirty flag before we assert. The buffer is marked dirty synchronously
    // on the keystroke regardless of whether the debounced write has fired.
    let resolveWrite: () => void = () => {};
    writeFile.mockImplementation(
      () =>
        new Promise<void>((res) => {
          resolveWrite = res;
        })
    );

    const { client } = renderWithClient(
      <CodeEditor sessionId="session-dirty" path="src/b.ts" />
    );

    await waitFor(() =>
      expect(screen.getByTestId("monaco")).toHaveValue("OLD")
    );

    // User types — marks the buffer dirty.
    fireEvent.change(screen.getByTestId("monaco"), {
      target: { value: "USER EDIT" },
    });
    expect(screen.getByTestId("monaco")).toHaveValue("USER EDIT");

    // A server refetch delivering different content must NOT overwrite the
    // unsaved edit.
    getFile.mockResolvedValue({
      path: "src/b.ts",
      content: "SERVER CHANGED",
      encoding: "utf-8",
      truncated: false,
    });
    await client.invalidateQueries({
      queryKey: ["file", "session-dirty", "src/b.ts"],
    });

    // Give the seed effect a chance to (incorrectly) fire.
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByTestId("monaco")).toHaveValue("USER EDIT");

    resolveWrite();
  });
});
