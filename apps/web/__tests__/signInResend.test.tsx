/**
 * P0-10 — resend behaviour on the sign-in post-send card.
 *
 * Covers:
 *   - The first send arms the 60-second timer.
 *   - The resend button is disabled while the timer is positive.
 *   - Clicking resend after the cooldown calls the API and re-arms.
 *   - A throttled response (``wait_seconds > 0``) re-arms the timer
 *     and surfaces a toast instead of an error.
 */

import * as React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

// ``useSearchParams`` is used by the sign-in page to surface the
// ``?error=github_oauth_failed`` toast and (B.2) to thread the
// ``?next=`` redirect target into the magic-link body. The default
// test env doesn't have these under jsdom, so stub no-op shapes that
// satisfy the ``get`` lookup and the router's ``replace`` call.
vi.mock("next/navigation", () => ({
  __esModule: true,
  useSearchParams: () => ({
    get: () => null,
  }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/auth/sign-in",
}));

const sendMagicLink = vi.fn();
const resendMagicLink = vi.fn();
const isGithubOAuthAvailable = vi.fn();
const startGithubOAuth = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    auth: {
      ...actual.auth,
      sendMagicLink: (...args: unknown[]) => sendMagicLink(...args),
      resendMagicLink: (...args: unknown[]) => resendMagicLink(...args),
      isGithubOAuthAvailable: (...args: unknown[]) =>
        isGithubOAuthAvailable(...args),
      startGithubOAuth: (...args: unknown[]) => startGithubOAuth(...args),
    },
  };
});

const toastSuccess = vi.fn();
const toastError = vi.fn();
const toastMessage = vi.fn();

vi.mock("sonner", () => ({
  __esModule: true,
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
    message: (...args: unknown[]) => toastMessage(...args),
  },
}));

import SignInPage from "@/app/auth/sign-in/page";

async function fillEmailAndSubmit(email: string) {
  fireEvent.change(screen.getByLabelText(/^email$/i), {
    target: { value: email },
  });
  fireEvent.click(screen.getByRole("button", { name: /email me/i }));
}

beforeEach(() => {
  vi.clearAllMocks();
  // Default behaviour: backend says GitHub OAuth is enabled so the
  // CTA renders inside the post-send card.
  isGithubOAuthAvailable.mockResolvedValue(true);
  sendMagicLink.mockResolvedValue(undefined);
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("sign-in resend (P0-10)", () => {
  it("arms the 60-second timer after the initial send", async () => {
    render(<SignInPage />);
    await fillEmailAndSubmit("alice@example.com");

    // The post-send card replaces the form.
    await waitFor(() =>
      expect(screen.getByTestId("post-send-card")).toBeInTheDocument(),
    );

    // The resend button is disabled with the countdown label.
    const resend = screen.getByTestId("resend-button");
    expect(resend).toBeDisabled();
    expect(resend).toHaveTextContent(/Resend in 60s/i);
  });

  it("disables the resend button while the timer is positive", async () => {
    render(<SignInPage />);
    await fillEmailAndSubmit("alice@example.com");
    await waitFor(() => screen.getByTestId("post-send-card"));

    const resend = screen.getByTestId("resend-button");

    // Advance halfway through the cooldown.
    await vi.advanceTimersByTimeAsync(30 * 1000);
    // The button should still be disabled with the updated counter.
    expect(resend).toBeDisabled();
    expect(resend).toHaveTextContent(/Resend in/i);

    // Clicking while disabled must not call the API.
    fireEvent.click(resend);
    expect(resendMagicLink).not.toHaveBeenCalled();
  });

  it("calls resendMagicLink and re-arms after the cooldown expires", async () => {
    resendMagicLink.mockResolvedValue({ wait_seconds: 0 });

    render(<SignInPage />);
    await fillEmailAndSubmit("alice@example.com");
    await waitFor(() => screen.getByTestId("post-send-card"));

    // Advance past the cooldown.
    await vi.advanceTimersByTimeAsync(61 * 1000);

    const resend = screen.getByTestId("resend-button");
    await waitFor(() => expect(resend).not.toBeDisabled());

    fireEvent.click(resend);
    await waitFor(() =>
      expect(resendMagicLink).toHaveBeenCalledWith({
        email: "alice@example.com",
      }),
    );

    // Re-armed at 60s.
    await waitFor(() =>
      expect(screen.getByTestId("resend-button")).toBeDisabled(),
    );
    expect(toastSuccess).toHaveBeenCalledWith(
      expect.stringMatching(/Link resent/i),
    );
  });

  it("surfaces a toast and re-arms when the server reports throttled", async () => {
    resendMagicLink.mockResolvedValue({ wait_seconds: 42 });

    render(<SignInPage />);
    await fillEmailAndSubmit("alice@example.com");
    await waitFor(() => screen.getByTestId("post-send-card"));

    // Skip the cooldown so the button is clickable.
    await vi.advanceTimersByTimeAsync(61 * 1000);
    await waitFor(() =>
      expect(screen.getByTestId("resend-button")).not.toBeDisabled(),
    );

    fireEvent.click(screen.getByTestId("resend-button"));
    await waitFor(() => expect(resendMagicLink).toHaveBeenCalledTimes(1));

    // The server's wait_seconds (42) takes precedence over our local 60.
    await waitFor(() =>
      expect(screen.getByTestId("resend-button")).toHaveTextContent(
        /Resend in 42s/i,
      ),
    );
    expect(toastMessage).toHaveBeenCalledWith(
      expect.stringMatching(/wait 42s/i),
    );
    expect(toastError).not.toHaveBeenCalled();
  });
});
