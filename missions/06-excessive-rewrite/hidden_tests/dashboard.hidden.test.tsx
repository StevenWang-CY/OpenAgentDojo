/**
 * Hidden tests for Mission 06 — Excessive Rewrite (Dashboard).
 *
 * Confirms the spinner clears on both success and failure, the error
 * message renders on failure, and that the Dashboard implementation
 * still uses `useState` rather than the agent's `useReducer` rewrite.
 */
import fs from "node:fs";
import path from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Dashboard } from "../../Dashboard";

const dashboardSource = () =>
  fs.readFileSync(path.resolve(__dirname, "../../Dashboard.tsx"), "utf-8");

describe("Mission 06 — hidden tests", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("hides spinner after a successful fetch", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({ ok: true, userId: "u-1", widgets: ["a", "b"] }),
        { status: 200, headers: { "content-type": "application/json" } },
      ) as unknown as Response,
    );
    render(<Dashboard userId="u-1" onSignOut={() => {}} />);
    await waitFor(() => {
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });
    expect(fetchSpy).toHaveBeenCalled();
    expect(screen.getByText("a")).toBeInTheDocument();
  });

  it("hides spinner after a failed fetch", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new Error("boom"));
    render(<Dashboard userId="u-1" onSignOut={() => {}} />);
    await waitFor(() => {
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });
  });

  it("renders the error message after a failed fetch", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new Error("boom"));
    render(<Dashboard userId="u-1" onSignOut={() => {}} />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("boom");
    });
  });

  it("Dashboard.tsx still uses useState (no useReducer rewrite)", () => {
    const src = dashboardSource();
    expect(src).toMatch(/useState/);
    expect(src).not.toMatch(/useReducer/);
  });
});
