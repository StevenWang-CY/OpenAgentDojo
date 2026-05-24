/**
 * P0-6 — DeletionLockBanner tests.
 *
 * Covers:
 *   - Banner only renders when ``me.deletion_scheduled_at`` is set (verified
 *     by mounting the banner directly with a scheduled timestamp).
 *   - The countdown text updates as the clock advances.
 *   - The banner uses ``tone="warning"`` (amber styling), NOT destructive.
 */
import * as React from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DeletionLockBanner } from "@/components/account/DeletionLockBanner";

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
    message: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

function renderBanner(scheduledFor: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DeletionLockBanner scheduledFor={scheduledFor} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  // Pin to a stable wall-clock so day/hour math is deterministic.
  vi.setSystemTime(new Date("2026-05-24T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("DeletionLockBanner", () => {
  it("renders with warning tone and a multi-day countdown", () => {
    const scheduled = new Date("2026-05-27T12:30:00Z").toISOString(); // +3d 30m
    renderBanner(scheduled);

    const banner = screen.getByTestId("deletion-lock-banner");
    expect(banner).toBeInTheDocument();
    expect(banner.getAttribute("data-tone")).toBe("warning");
    expect(screen.getByTestId("deletion-countdown")).toHaveTextContent(
      /3 days?/,
    );
  });

  it("re-renders the countdown when the clock advances", async () => {
    const scheduled = new Date("2026-05-24T12:10:00Z").toISOString(); // +10 minutes
    renderBanner(scheduled);

    expect(screen.getByTestId("deletion-countdown")).toHaveTextContent(/10m/);

    // Advance 3 minutes — the next minute tick should re-render with "6m"
    // (we floor the remaining-minutes, so 6m59s → 6m, not 7m).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3 * 60 * 1000 + 100);
    });
    expect(screen.getByTestId("deletion-countdown")).toHaveTextContent(/6m/);
  });
});
