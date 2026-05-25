/**
 * P0-9 — HelpOverlay tests.
 *
 * Asserts the FE contract:
 *  - Renders the shortcut table + supervision tips when ``open`` is true.
 *  - Toggling the "don't show on startup" checkbox writes localStorage.
 *  - The ``shouldAutoOpenHelp`` helper returns the inverse of the
 *    persisted preference.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  HelpOverlay,
  shouldAutoOpenHelp,
} from "@/components/workspace/HelpOverlay";

beforeEach(() => {
  window.localStorage.clear();
});

describe("HelpOverlay", () => {
  it("renders the shortcut table when open", async () => {
    const onOpenChange = vi.fn();
    render(<HelpOverlay open={true} onOpenChange={onOpenChange} />);

    expect(await screen.findByText(/quick open file/i)).toBeInTheDocument();
    expect(await screen.findByText(/find in files/i)).toBeInTheDocument();
    expect(await screen.findByText(/toggle this help/i)).toBeInTheDocument();
    expect(
      await screen.findByText(/supervision tips/i),
    ).toBeInTheDocument();
  });

  it("does not render when closed", () => {
    render(<HelpOverlay open={false} onOpenChange={vi.fn()} />);
    expect(screen.queryByTestId("help-overlay")).not.toBeInTheDocument();
  });

  it("persists the don't-show-on-startup preference", async () => {
    render(<HelpOverlay open={true} onOpenChange={vi.fn()} />);
    const checkbox = await screen.findByTestId("help-overlay-suppress");
    expect(window.localStorage.getItem("oad.help.suppressOnStart")).toBeNull();

    fireEvent.click(checkbox);
    expect(window.localStorage.getItem("oad.help.suppressOnStart")).toBe(
      "true",
    );
    expect(shouldAutoOpenHelp()).toBe(false);

    fireEvent.click(checkbox);
    expect(window.localStorage.getItem("oad.help.suppressOnStart")).toBeNull();
    expect(shouldAutoOpenHelp()).toBe(true);
  });

  it("shouldAutoOpenHelp returns true by default", () => {
    expect(shouldAutoOpenHelp()).toBe(true);
  });
});
