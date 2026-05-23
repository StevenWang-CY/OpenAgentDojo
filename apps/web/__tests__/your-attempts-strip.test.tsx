/**
 * P0-3 — YourAttemptsStrip component tests.
 *
 * Asserts the private mission-detail strip honours the ADR 0009 contract:
 *  - count == 0 → strip stays hidden entirely.
 *  - count >= 1 → renders best + latest + delta with appropriate signs.
 *  - best_was_gave_up renders the "gave up" indicator beside the score.
 */
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { YourAttemptsStrip } from "@/components/catalog/YourAttemptsStrip";

describe("YourAttemptsStrip", () => {
  it("renders nothing when count is zero", () => {
    const { container } = render(
      <YourAttemptsStrip
        attempts={{
          count: 0,
          best_score: null,
          best_submission_id: null,
          latest_score: null,
          latest_submission_id: null,
          delta: null,
          best_was_gave_up: false,
          score_history: [],
        }}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders best + latest + delta when the user has multiple attempts", () => {
    render(
      <YourAttemptsStrip
        attempts={{
          count: 3,
          best_score: 78,
          best_submission_id: "best-id",
          latest_score: 72,
          latest_submission_id: "latest-id",
          delta: 6,
          best_was_gave_up: false,
          score_history: [66, 78, 72],
        }}
      />,
    );

    const strip = screen.getByTestId("your-attempts-strip");
    expect(strip).toBeTruthy();
    expect(strip.textContent).toMatch(/3× attempted/);
    expect(strip.textContent).toMatch(/78/);
    expect(strip.textContent).toMatch(/72/);
    // Delta shown as +6 (improving).
    expect(strip.textContent).toMatch(/\+6/);
    // The "view" links should point at the best + latest submissions.
    const links = Array.from(strip.querySelectorAll("a")).map(
      (a) => a.getAttribute("href"),
    );
    expect(links).toContain("/report/best-id");
    expect(links).toContain("/report/latest-id");
  });

  it("renders the gave-up chip when the user's best attempt was a give-up", () => {
    render(
      <YourAttemptsStrip
        attempts={{
          count: 1,
          best_score: 50,
          best_submission_id: "gave-up-id",
          latest_score: 50,
          latest_submission_id: "gave-up-id",
          delta: null,
          best_was_gave_up: true,
          score_history: [50],
        }}
      />,
    );

    const strip = screen.getByTestId("your-attempts-strip");
    expect(strip.textContent).toMatch(/gave up/i);
    // Delta is null for single-attempt users → renders as "—".
    expect(strip.textContent).toMatch(/—/);
  });

  it("renders a negative delta with a down arrow when the user regressed", () => {
    render(
      <YourAttemptsStrip
        attempts={{
          count: 2,
          best_score: 78,
          best_submission_id: "best-id",
          latest_score: 60,
          latest_submission_id: "latest-id",
          delta: -18,
          best_was_gave_up: false,
          score_history: [78, 60],
        }}
      />,
    );

    const strip = screen.getByTestId("your-attempts-strip");
    expect(strip.textContent).toMatch(/-18/);
  });

  it("reveals the sparkline on delta hover when score history is available", () => {
    render(
      <YourAttemptsStrip
        attempts={{
          count: 4,
          best_score: 88,
          best_submission_id: "best-id",
          latest_score: 84,
          latest_submission_id: "latest-id",
          delta: 8,
          best_was_gave_up: false,
          score_history: [66, 72, 88, 84],
        }}
      />,
    );

    // Tooltip is hidden until hover.
    expect(screen.queryByTestId("delta-sparkline")).toBeNull();
    const trigger = screen.getByTestId("delta-trigger") as HTMLButtonElement;
    expect(trigger.disabled).toBe(false);
    fireEvent.mouseEnter(trigger);
    const tooltip = screen.getByTestId("delta-sparkline");
    expect(tooltip).toBeTruthy();
    // SVG polyline should have one (x,y) per history point.
    const poly = tooltip.querySelector("polyline");
    expect(poly).not.toBeNull();
    const pts = (poly?.getAttribute("points") ?? "").trim().split(/\s+/);
    expect(pts.length).toBe(4);
  });

  it("disables the sparkline trigger when fewer than two attempts exist", () => {
    render(
      <YourAttemptsStrip
        attempts={{
          count: 1,
          best_score: 60,
          best_submission_id: "best-id",
          latest_score: 60,
          latest_submission_id: "best-id",
          delta: 0,
          best_was_gave_up: false,
          score_history: [60],
        }}
      />,
    );
    const trigger = screen.getByTestId("delta-trigger") as HTMLButtonElement;
    expect(trigger.disabled).toBe(true);
  });
});
