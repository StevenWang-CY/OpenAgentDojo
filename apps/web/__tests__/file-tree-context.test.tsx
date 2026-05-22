import * as React from "react";
import { act, render } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import type { FileTreeNode } from "@arena/shared-types";

const setContext = vi.fn().mockResolvedValue(undefined);
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    setContext: (...args: unknown[]) => setContext(...args),
  };
});

import { FileTree } from "@/components/workspace/FileTree";

const NODES: FileTreeNode[] = [
  {
    path: "backend",
    name: "backend",
    kind: "directory",
    size: null,
    children: [
      {
        path: "backend/session.ts",
        name: "session.ts",
        kind: "file",
        size: 200,
      },
    ],
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("FileTree → POST /context", () => {
  it("debounces and posts the latest selection to /context", async () => {
    const { rerender } = render(
      <FileTree
        nodes={NODES}
        sessionId="session-1"
        selectedContext={[]}
        onToggleContext={() => {}}
      />
    );

    // Simulate a toggle by re-rendering with a new selection — the
    // controlled-component contract is the same as what `WorkspaceShell`
    // does in real life.
    rerender(
      <FileTree
        nodes={NODES}
        sessionId="session-1"
        selectedContext={["backend/session.ts"]}
        onToggleContext={() => {}}
      />
    );

    // Before the debounce window elapses, no POST should have fired.
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(setContext).not.toHaveBeenCalled();

    // After 500ms total, the POST should fire exactly once.
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(setContext).toHaveBeenCalledTimes(1);
    expect(setContext).toHaveBeenCalledWith("session-1", {
      files: ["backend/session.ts"],
      logs: [],
      tests: [],
      extras: [],
    });
  });

  it("does not POST when sessionId is omitted", () => {
    render(
      <FileTree
        nodes={NODES}
        selectedContext={["backend/session.ts"]}
        onToggleContext={() => {}}
      />
    );
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(setContext).not.toHaveBeenCalled();
  });

  it("does not flush stale selection to a re-keyed session on unmount", () => {
    // Mount with sessionA + an outstanding selection toggle that hasn't
    // been flushed (we unmount before the debounce window elapses).
    const { rerender, unmount } = render(
      <FileTree
        nodes={NODES}
        sessionId="session-a"
        selectedContext={[]}
        onToggleContext={() => {}}
      />
    );

    rerender(
      <FileTree
        nodes={NODES}
        sessionId="session-a"
        selectedContext={["backend/session.ts"]}
        onToggleContext={() => {}}
      />
    );

    // Flip the sessionId via a re-render (the workspace shell does this
    // when the user navigates to a different session). The mount-time
    // session id was "session-a" — the unmount flush must NOT post to
    // "session-b", because that would overwrite the new session's
    // selection on the backend.
    rerender(
      <FileTree
        nodes={NODES}
        sessionId="session-b"
        selectedContext={["backend/session.ts"]}
        onToggleContext={() => {}}
      />
    );

    // Tear down before either debounce window can fire.
    act(() => {
      unmount();
    });

    expect(setContext).not.toHaveBeenCalled();
  });
});
