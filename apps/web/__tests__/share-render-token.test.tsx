/**
 * p1-share-render-no-token — the report ShareDropdown's RenderItem must
 * thread the share token into the render-status poll.
 *
 * An anonymous viewer landing on /report/{id}?share=<jwt> who clicks
 * "Download PDF" / "Download PNG" previously sent no ``?share=`` (and no
 * cookie), so the backend 401'd and the menu flipped to "failed" — while
 * the replay JSON/ZIP items in the SAME dropdown succeeded because they
 * already threaded the token. The fix passes ``share`` down to RenderItem
 * and forwards it via ``getReportRenderStatus(submissionId, kind, share, …)``.
 *
 * Rather than mock ``getReportRenderStatus`` (which would hide the bug —
 * the regression is the hardcoded ``undefined`` arg), we exercise the real
 * api fn against a stubbed ``fetch`` and assert the request URL carries the
 * ``share=`` query param.
 */
import * as React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// telemetry + sonner are noisy in jsdom; stub the minimal surface the
// dropdown touches so the render-status path runs cleanly.
vi.mock("@/lib/telemetry", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/telemetry")>("@/lib/telemetry");
  return { ...actual, track: vi.fn() };
});
vi.mock("sonner", () => ({
  toast: {
    promise: vi.fn((p: Promise<unknown>) => p),
    error: vi.fn(),
    success: vi.fn(),
    message: vi.fn(),
  },
}));

import { ShareDropdown } from "@/components/report/ShareDropdown";

const SUBMISSION_ID = "11111111-2222-3333-4444-555555555555";
const SHARE_TOKEN = "eyJhbGciOiJIUzI1NiJ9.share.jwt";

// Capture every fetch URL the dropdown issues so we can assert on the
// render-status request specifically.
let fetchUrls: string[] = [];
let originalFetch: typeof globalThis.fetch;

beforeEach(() => {
  fetchUrls = [];
  originalFetch = globalThis.fetch;
  // 202 = queued/running — keeps the dropdown in its polling state without
  // navigating or scheduling a real interval inside the synchronous click.
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    fetchUrls.push(typeof input === "string" ? input : input.toString());
    return new Response(
      JSON.stringify({
        submission_id: SUBMISSION_ID,
        kind: "pdf",
        status: "queued",
        url: null,
        error: null,
      }),
      { status: 202, headers: { "content-type": "application/json" } },
    );
  }) as unknown as typeof globalThis.fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.clearAllMocks();
});

function renderDropdown(share: string | null) {
  return render(
    <ShareDropdown
      submissionId={SUBMISSION_ID}
      onCopyLink={() => undefined}
      sharing={false}
      share={share}
    />,
  );
}

async function openDropdown() {
  await act(async () => {
    fireEvent.click(screen.getByTestId("share-dropdown-trigger"));
  });
}

function renderStatusUrls(): string[] {
  return fetchUrls.filter((u) => u.includes("/render"));
}

describe("ShareDropdown — render token threading (p1-share-render-no-token)", () => {
  it("threads the share token into the PDF render-status request", async () => {
    renderDropdown(SHARE_TOKEN);
    await openDropdown();

    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: /Download PDF/i }));
    });

    await waitFor(() => expect(renderStatusUrls().length).toBeGreaterThan(0));
    const url = renderStatusUrls()[0]!;
    expect(url).toContain("kind=pdf");
    expect(url).toContain(`share=${encodeURIComponent(SHARE_TOKEN)}`);
  });

  it("threads the share token into the PNG render-status request", async () => {
    renderDropdown(SHARE_TOKEN);
    await openDropdown();

    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: /Download PNG/i }));
    });

    await waitFor(() => expect(renderStatusUrls().length).toBeGreaterThan(0));
    const url = renderStatusUrls()[0]!;
    expect(url).toContain("kind=png");
    expect(url).toContain(`share=${encodeURIComponent(SHARE_TOKEN)}`);
  });

  it("omits the share param for the owner path (share=null)", async () => {
    renderDropdown(null);
    await openDropdown();

    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: /Download PDF/i }));
    });

    await waitFor(() => expect(renderStatusUrls().length).toBeGreaterThan(0));
    expect(renderStatusUrls()[0]!).not.toContain("share=");
  });
});
