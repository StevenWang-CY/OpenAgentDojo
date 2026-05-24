/**
 * P0-6 — AccountView shell tests.
 *
 * Covers:
 *   - Tabs render and switch on click; URL search param updates accordingly.
 *   - Profile tab shows current display_name and PATCHes /me on submit.
 *   - Email change pending state renders when ``me.pending_email`` is set.
 *   - Email change form submits; 409 surfaces the conflict error in the form.
 */
import * as React from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import type { User } from "@arena/shared-types";
import { API_BASE, server } from "./contract/_setup";
import { AccountView } from "@/components/account/AccountView";

const pushMock = vi.fn();
let currentSearch = new URLSearchParams();
const setSearch = (search: string) => {
  currentSearch = new URLSearchParams(search);
};

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: (href: string) => {
      pushMock(href);
      const q = href.split("?")[1] ?? "";
      setSearch(q);
    },
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
  id: "00000000-0000-0000-0000-000000000123",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  github_login: null,
  handle: "ada",
  created_at: "2025-01-01T00:00:00Z",
  last_login_at: "2026-05-20T08:00:00Z",
  csrf_token: "csrf-deadbeef",
  tutorial_completed_at: null,
  tutorial_replay_count: 0,
  pending_email: null,
  deletion_scheduled_at: null,
};

function renderAccount(user: User = baseUser) {
  setSearch("");
  server.use(
    http.get(`${API_BASE}/api/v1/auth/me`, () => HttpResponse.json(user)),
    http.get(`${API_BASE}/api/v1/auth/me/consent`, () =>
      HttpResponse.json({ analytics: null, functional: null, marketing: null }),
    ),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AccountView initialTab="profile" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  pushMock.mockClear();
  server.listen({ onUnhandledRequest: "bypass" });
});

afterEach(() => {
  server.resetHandlers();
  server.close();
});

describe("AccountView", () => {
  it("renders all four tabs and switches on click, updating the URL", async () => {
    renderAccount();
    await screen.findByTestId("tab-profile");

    expect(screen.getByTestId("tab-profile")).toBeInTheDocument();
    expect(screen.getByTestId("tab-privacy")).toBeInTheDocument();
    expect(screen.getByTestId("tab-data")).toBeInTheDocument();
    expect(screen.getByTestId("tab-danger")).toBeInTheDocument();

    const dangerTab = screen.getByTestId("tab-danger");
    // Radix Tabs in jsdom only registers selection on the mousedown event;
    // a plain `fireEvent.click` slips through. Fire both so the trigger
    // behaves the way it would in a real browser.
    fireEvent.mouseDown(dangerTab);
    fireEvent.click(dangerTab);
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/account?tab=danger"));
  });

  it("populates the display_name field from /me and PATCHes on save", async () => {
    const patched = vi.fn();
    server.use(
      http.patch(`${API_BASE}/api/v1/auth/me`, async ({ request }) => {
        patched(await request.json());
        return HttpResponse.json({ ...baseUser, display_name: "Ada N" });
      }),
    );

    renderAccount();
    const input = (await screen.findByTestId("display-name-input")) as HTMLInputElement;
    expect(input.value).toBe("Ada Lovelace");

    fireEvent.change(input, { target: { value: "Ada N" } });
    fireEvent.click(screen.getByTestId("save-profile"));

    await waitFor(() => expect(patched).toHaveBeenCalled());
    expect(patched).toHaveBeenCalledWith({ display_name: "Ada N" });
  });

  it("renders the pending-email banner when me.pending_email is set", async () => {
    renderAccount({ ...baseUser, pending_email: "ada-new@example.com" });
    expect(await screen.findByTestId("email-pending")).toHaveTextContent(
      "ada-new@example.com",
    );
  });

  it("submits the email change form and surfaces a 409 conflict in-form", async () => {
    server.use(
      http.post(`${API_BASE}/api/v1/auth/me/email/change`, () =>
        HttpResponse.json({ detail: "in use" }, { status: 409 }),
      ),
    );

    renderAccount();
    fireEvent.click(await screen.findByTestId("open-email-change"));
    const newEmailInput = (await screen.findByTestId(
      "new-email-input",
    )) as HTMLInputElement;
    fireEvent.change(newEmailInput, {
      target: { value: "ada-new@example.com" },
    });
    fireEvent.click(screen.getByTestId("submit-email-change"));

    const form = await screen.findByTestId("email-change-form");
    await waitFor(() => {
      expect(within(form).getByRole("alert")).toHaveTextContent(
        /already in use/i,
      );
    });
  });

  it("surfaces the dedicated email_unchanged copy on a 400 envelope (P1-4)", async () => {
    // The backend returns 400 with the structured ``{detail: {code,
    // message}}`` envelope for ``email_unchanged``. The FE must branch on
    // the body's code (not just the status), so a future status shuffle
    // doesn't fall through to the generic ``[object Object]`` toast.
    server.use(
      http.post(`${API_BASE}/api/v1/auth/me/email/change`, () =>
        HttpResponse.json(
          {
            detail: {
              code: "email_unchanged",
              message:
                "Your new email cannot match your current address.",
            },
          },
          { status: 400 },
        ),
      ),
    );

    renderAccount();
    fireEvent.click(await screen.findByTestId("open-email-change"));
    const newEmailInput = (await screen.findByTestId(
      "new-email-input",
    )) as HTMLInputElement;
    fireEvent.change(newEmailInput, {
      target: { value: "fresh@example.com" }, // bypass the FE pre-flight
    });
    fireEvent.click(screen.getByTestId("submit-email-change"));

    const form = await screen.findByTestId("email-change-form");
    await waitFor(() => {
      expect(within(form).getByRole("alert")).toHaveTextContent(
        /already your current email address/i,
      );
    });
  });
});
