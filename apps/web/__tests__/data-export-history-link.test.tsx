/**
 * P2 fix — the Data tab "See your mission history" link must point at the
 * viewer's RESOLVED handle (``/profile/{handle}``), not the hardcoded
 * ``/profile/me``.
 *
 * ``GET /profiles/me`` resolves by literal handle equality, so it 404s for
 * every user whose handle isn't the string "me" (only ``/profiles/me/skills``
 * is a real alias). The link therefore has to read the handle off the same
 * ``["me"]`` / ``auth.me`` cache the rest of the account surface uses —
 * mirroring the Header — and omit the link entirely until the handle lands.
 */
import * as React from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import type { User } from "@arena/shared-types";
import { API_BASE, server } from "./contract/_setup";
import { DataExportPanel } from "@/components/account/DataExportPanel";

vi.mock("next/link", () => ({
  __esModule: true,
  default: ({
    children,
    href,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
  } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
    message: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

const baseUser: User = {
  id: "00000000-0000-0000-0000-000000000123",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  github_login: null,
  github_avatar_url: null,
  github_html_url: null,
  github_verified_at: null,
  handle: "ada",
  created_at: "2025-01-01T00:00:00Z",
  last_login_at: "2026-05-20T08:00:00Z",
  csrf_token: "csrf-deadbeef",
  tutorial_completed_at: null,
  tutorial_replay_count: 0,
  pending_email: null,
  deletion_scheduled_at: null,
};

function renderPanel(user: User = baseUser) {
  server.use(
    http.get(`${API_BASE}/api/v1/auth/me`, () => HttpResponse.json(user)),
    http.get(`${API_BASE}/api/v1/auth/me/data-export/latest`, () =>
      new HttpResponse(null, { status: 204 }),
    ),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DataExportPanel locked={false} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  server.listen({ onUnhandledRequest: "bypass" });
});

afterEach(() => {
  server.resetHandlers();
  server.close();
});

describe("DataExportPanel — mission-history link", () => {
  it("links to /profile/{handle}, never the broken /profile/me", async () => {
    renderPanel();

    const link = await screen.findByTestId("export-history-link");
    expect(link).toHaveAttribute("href", "/profile/ada");
    // Regression guard: the old hardcoded href 404s for any non-"me" handle.
    expect(link).not.toHaveAttribute("href", "/profile/me");
  });

  it("omits the link when the viewer has no handle yet (no broken /profile/me)", async () => {
    renderPanel({ ...baseUser, handle: null });

    // The export card itself renders off /latest; once it lands we know the
    // handle decision is final. We assert no link at all rather than a link
    // to the broken /profile/me.
    await screen.findByTestId("export-card");
    expect(screen.queryByTestId("export-history-link")).toBeNull();
    expect(
      screen.queryByRole("link", { name: /mission history/i }),
    ).toBeNull();
  });
});
