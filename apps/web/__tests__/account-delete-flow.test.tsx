/**
 * P0-6 — Account deletion flow.
 *
 * Covers the high-friction confirm + the deletion-lock state transition:
 *   - The destructive button is disabled until the user re-types the email
 *     exactly.
 *   - On success the lock banner appears at the top of the page.
 *   - The Cancel-deletion CTA clears the banner via API call.
 *   - The banner uses ``tone="warning"`` (amber), NOT destructive.
 */
import * as React from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import type { User } from "@arena/shared-types";
import { API_BASE, server } from "./contract/_setup";
import { AccountView } from "@/components/account/AccountView";

let currentSearch = new URLSearchParams("tab=danger");
const setSearch = (q: string) => (currentSearch = new URLSearchParams(q));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: (href: string) => setSearch(href.split("?")[1] ?? ""),
    replace: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/account",
  useSearchParams: () => currentSearch,
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
  id: "00000000-0000-0000-0000-000000000456",
  email: "delete-me@example.com",
  display_name: "Test User",
  github_login: null,
  github_avatar_url: null,
  github_html_url: null,
  github_verified_at: null,
  handle: "deleteme",
  created_at: "2025-01-01T00:00:00Z",
  last_login_at: "2026-05-20T08:00:00Z",
  csrf_token: "csrf-deadbeef",
  tutorial_completed_at: null,
  tutorial_replay_count: 0,
  pending_email: null,
  deletion_scheduled_at: null,
};

function renderWithUser(user: User) {
  let currentUser = user;
  server.use(
    http.get(`${API_BASE}/api/v1/auth/me`, () => HttpResponse.json(currentUser)),
    http.get(`${API_BASE}/api/v1/auth/me/consent`, () =>
      HttpResponse.json({ analytics: null, functional: null, marketing: null }),
    ),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <AccountView initialTab="danger" />
    </QueryClientProvider>,
  );
  return {
    ...view,
    setUser(next: User) {
      currentUser = next;
      client.invalidateQueries({ queryKey: ["me"] });
    },
  };
}

beforeEach(() => {
  setSearch("tab=danger");
  server.listen({ onUnhandledRequest: "bypass" });
});

afterEach(() => {
  server.resetHandlers();
  server.close();
});

describe("Account deletion flow", () => {
  it("disables the destructive submit until the email matches exactly", async () => {
    renderWithUser(baseUser);

    fireEvent.click(await screen.findByTestId("open-delete-dialog"));

    const input = (await screen.findByTestId("confirm-email-input")) as HTMLInputElement;
    const submit = screen.getByTestId("confirm-delete") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    fireEvent.change(input, { target: { value: "wrong@example.com" } });
    expect(submit.disabled).toBe(true);

    fireEvent.change(input, { target: { value: baseUser.email } });
    expect(submit.disabled).toBe(false);
  });

  it("schedules deletion, shows the lock banner, and cancels via the banner CTA", async () => {
    const scheduledFor = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
    let userState: User = baseUser;

    server.use(
      http.get(`${API_BASE}/api/v1/auth/me`, () => HttpResponse.json(userState)),
      http.post(`${API_BASE}/api/v1/auth/me/delete`, () => {
        userState = { ...baseUser, deletion_scheduled_at: scheduledFor };
        return HttpResponse.json({ scheduled_for: scheduledFor });
      }),
      http.post(`${API_BASE}/api/v1/auth/me/delete/cancel`, () => {
        userState = { ...baseUser, deletion_scheduled_at: null };
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={client}>
        <AccountView initialTab="danger" />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByTestId("open-delete-dialog"));
    const input = (await screen.findByTestId("confirm-email-input")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: baseUser.email } });
    fireEvent.click(screen.getByTestId("confirm-delete"));

    const banner = await screen.findByTestId("deletion-lock-banner");
    expect(banner).toBeInTheDocument();
    expect(banner.getAttribute("data-tone")).toBe("warning");

    // The Danger tab card also flips to the scheduled view.
    await waitFor(() =>
      expect(screen.getByTestId("danger-scheduled-card")).toBeInTheDocument(),
    );

    // Cancel via the banner's CTA, which should clear the banner on the
    // next /me refetch.
    fireEvent.click(screen.getByTestId("cancel-deletion-banner"));
    await waitFor(() =>
      expect(screen.queryByTestId("deletion-lock-banner")).not.toBeInTheDocument(),
    );
  });
});
