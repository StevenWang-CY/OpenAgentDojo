/**
 * P1 — TestPanel quick-check actions are derived from the mission's
 * ``language_runtime`` so a Go or Python sandbox (which has no ``pnpm``)
 * gets real commands instead of the Node trio that would exit 127.
 *
 * Asserts:
 *   - ``actionsForRuntime`` maps each runtime to the right command trio.
 *   - The rendered TestPanel fires the runtime-appropriate command (not a
 *     ``pnpm`` command) for a go122 / python312 mission, and the Node trio
 *     when the runtime is absent.
 */

import * as React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { FlaskConical } from "lucide-react";

const runCommand = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    runCommand: (...args: unknown[]) => runCommand(...args),
  };
});

vi.mock("sonner", () => ({
  toast: { warning: vi.fn(), error: vi.fn(), success: vi.fn() },
}));

import { TestPanel, actionsForRuntime } from "@/components/workspace/TestPanel";

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
  runCommand.mockResolvedValue({ exit_code: 0, duration_ms: 120 });
});

describe("actionsForRuntime", () => {
  it("maps python312 to pytest / mypy / ruff (never pnpm)", () => {
    const actions = actionsForRuntime("python312");
    const commands = actions.map((a) => a.command);
    expect(commands).toEqual(["pytest", "mypy .", "ruff check ."]);
    expect(commands.some((c) => c.includes("pnpm"))).toBe(false);
  });

  it("maps go122 to go test / go vet / gofmt (never pnpm)", () => {
    const actions = actionsForRuntime("go122");
    const commands = actions.map((a) => a.command);
    expect(commands).toEqual(["go test ./...", "go vet ./...", "gofmt -l ."]);
    expect(commands.some((c) => c.includes("pnpm"))).toBe(false);
  });

  it("maps node20 (and null/undefined) to the pnpm trio", () => {
    const node = actionsForRuntime("node20").map((a) => a.command);
    expect(node).toEqual(["pnpm test:unit", "pnpm typecheck", "pnpm lint"]);
    expect(actionsForRuntime(null).map((a) => a.command)).toEqual(node);
    expect(actionsForRuntime(undefined).map((a) => a.command)).toEqual(node);
  });

  it("preserves the test/typecheck/lint categories per runtime", () => {
    for (const rt of ["node20", "python312", "go122"] as const) {
      expect(actionsForRuntime(rt).map((a) => a.category)).toEqual([
        "test",
        "typecheck",
        "lint",
      ]);
    }
  });
});

describe("TestPanel — runtime-derived buttons", () => {
  it("runs a go test command (not pnpm) for a go122 mission", async () => {
    renderWithClient(
      <TestPanel sessionId="session-go" languageRuntime="go122" />
    );

    const testBtn = screen.getByRole("button", { name: /test/i });
    fireEvent.click(testBtn);

    await waitFor(() => expect(runCommand).toHaveBeenCalledTimes(1));
    expect(runCommand).toHaveBeenCalledWith("session-go", {
      command: "go test ./...",
      category: "test",
    });
    // No pnpm command was ever dispatched.
    expect(
      runCommand.mock.calls.some(([, body]) =>
        String((body as { command: string }).command).includes("pnpm")
      )
    ).toBe(false);
  });

  it("runs pytest (not pnpm) for a python312 mission", async () => {
    renderWithClient(
      <TestPanel sessionId="session-py" languageRuntime="python312" />
    );

    const testBtn = screen.getByRole("button", { name: /pytest/i });
    fireEvent.click(testBtn);

    await waitFor(() => expect(runCommand).toHaveBeenCalledTimes(1));
    expect(runCommand).toHaveBeenCalledWith("session-py", {
      command: "pytest",
      category: "test",
    });
  });

  it("falls back to the pnpm trio when no runtime is supplied", async () => {
    renderWithClient(<TestPanel sessionId="session-node" />);

    const unitBtn = screen.getByRole("button", { name: /unit/i });
    fireEvent.click(unitBtn);

    await waitFor(() => expect(runCommand).toHaveBeenCalledTimes(1));
    expect(runCommand).toHaveBeenCalledWith("session-node", {
      command: "pnpm test:unit",
      category: "test",
    });
  });

  it("lets an explicit actions prop override the runtime", async () => {
    renderWithClient(
      <TestPanel
        sessionId="session-x"
        languageRuntime="go122"
        actions={[
          {
            category: "test",
            command: "custom-runner",
            label: "Custom",
            icon: FlaskConical,
          },
        ]}
      />
    );

    const btn = screen.getByRole("button", { name: /custom/i });
    fireEvent.click(btn);

    await waitFor(() => expect(runCommand).toHaveBeenCalledTimes(1));
    expect(runCommand).toHaveBeenCalledWith("session-x", {
      command: "custom-runner",
      category: "test",
    });
  });
});
