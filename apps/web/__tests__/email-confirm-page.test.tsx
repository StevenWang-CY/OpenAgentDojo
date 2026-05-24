/**
 * P0-6 — Email-confirm magic-link callback page.
 *
 * Covers:
 *   - With ``?token=foo`` on mount, the page calls
 *     ``account.confirmEmailChange`` and renders the success state.
 *   - On 400 we render "this link is invalid or has expired."
 *   - On 409 (``{code: "no_pending_email"}``) we render the dedicated
 *     "nothing to confirm" state so the user knows the link itself wasn't
 *     malformed.
 */
import * as React from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import type { User } from "@arena/shared-types";
import { API_BASE, server } from "./contract/_setup";

let currentSearch = new URLSearchParams("token=fresh-token");

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/auth/email-confirm",
  useSearchParams: () => currentSearch,
}));

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

import EmailConfirmPage from "@/app/auth/email-confirm/page";

// The page renders ``<Header />`` inline (per the audit fix that wrapped
// the callback in the system chrome). Header reads from React Query, so
// every test render needs a QueryClient — wrap in a thin helper to avoid
// repeating the boilerplate three times.
function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <EmailConfirmPage />
    </QueryClientProvider>,
  );
}

const updatedUser: User = {
  id: "00000000-0000-0000-0000-0000000abcde",
  email: "new@example.com",
  display_name: "Ada N",
  github_login: null,
  handle: "adan",
  created_at: "2025-01-01T00:00:00Z",
  last_login_at: "2026-05-20T08:00:00Z",
  csrf_token: "csrf-fresh",
  tutorial_completed_at: null,
  tutorial_replay_count: 0,
  pending_email: null,
  deletion_scheduled_at: null,
};

beforeEach(() => {
  currentSearch = new URLSearchParams("token=fresh-token");
  server.listen({ onUnhandledRequest: "bypass" });
});

afterEach(() => {
  server.resetHandlers();
  server.close();
});

describe("EmailConfirmPage", () => {
  it("calls confirmEmailChange on mount and renders the success state", async () => {
    const seen = vi.fn();
    server.use(
      http.post(
        `${API_BASE}/api/v1/auth/me/email/confirm`,
        async ({ request }) => {
          seen(await request.json());
          return HttpResponse.json(updatedUser);
        },
      ),
    );

    renderPage();

    await screen.findByTestId("email-confirm-success");
    await waitFor(() =>
      expect(seen).toHaveBeenCalledWith({ token: "fresh-token" }),
    );
    expect(screen.getByText(/new@example.com/)).toBeInTheDocument();
  });

  it("renders the invalid-link state on 400", async () => {
    server.use(
      http.post(`${API_BASE}/api/v1/auth/me/email/confirm`, () =>
        HttpResponse.json({ detail: "invalid" }, { status: 400 }),
      ),
    );

    renderPage();

    const card = await screen.findByTestId("email-confirm-invalid");
    expect(card).toHaveTextContent(/invalid or has expired/i);
  });

  it("renders the no-pending state on 409 (no_pending_email)", async () => {
    server.use(
      http.post(`${API_BASE}/api/v1/auth/me/email/confirm`, () =>
        HttpResponse.json(
          { detail: "no pending email change", code: "no_pending_email" },
          { status: 409 },
        ),
      ),
    );

    renderPage();

    const card = await screen.findByTestId("email-confirm-no-pending");
    expect(card).toHaveTextContent(/no pending email change/i);
  });

  it("renders the distinct server-error state on 5xx (P1-11)", async () => {
    // Previously a 5xx fell through to the generic "couldn't reach the
    // server" copy that's reserved for true connectivity failures.
    server.use(
      http.post(`${API_BASE}/api/v1/auth/me/email/confirm`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 503 }),
      ),
    );

    renderPage();

    const card = await screen.findByTestId("email-confirm-server");
    expect(card).toHaveTextContent(/server hit a snag/i);
    expect(card.querySelector("button")).toBeTruthy();
  });

  it("renders the rate-limited state on 429 (P1-11)", async () => {
    server.use(
      http.post(`${API_BASE}/api/v1/auth/me/email/confirm`, () =>
        HttpResponse.json(
          { detail: "rate limit" },
          { status: 429, headers: { "retry-after": "30" } },
        ),
      ),
    );

    renderPage();

    const card = await screen.findByTestId("email-confirm-rate-limited");
    expect(card).toHaveTextContent(/rate limit/i);
    expect(card).toHaveTextContent(/wait a minute and retry/i);
  });

  it("threads an AbortSignal into confirmEmailChange and aborts on unmount (P1-11)", async () => {
    // Spy on the API wrapper so we can inspect the AbortSignal the page
    // threads through. We can't rely on MSW's ``request.signal`` because
    // the version in use doesn't bridge node-fetch's controller to the
    // handler reliably.
    const { account } = await import("@/lib/api");
    const captured: AbortSignal[] = [];
    const spy = vi
      .spyOn(account, "confirmEmailChange")
      .mockImplementation((_input, signal) => {
        if (signal) captured.push(signal);
        return new Promise(() => undefined);
      });

    const { unmount } = renderPage();
    expect(screen.getByText(/confirming your new email/i)).toBeInTheDocument();
    await waitFor(() => expect(captured.length).toBeGreaterThan(0));
    const signal = captured[0];
    if (!signal) throw new Error("expected an AbortSignal to be captured");
    expect(signal.aborted).toBe(false);

    unmount();
    expect(signal.aborted).toBe(true);
    spy.mockRestore();
  });
});
