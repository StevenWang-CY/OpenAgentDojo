/**
 * P0-7 — Sign-in page GitHub button visibility.
 *
 * Asserts the FE only renders the "Continue with GitHub" button when the
 * backend probe ``auth.isGithubOAuthAvailable()`` resolves true. When it
 * resolves false (or throws), the button must stay hidden so the user
 * never gets a path that would 503 on click.
 */

import * as React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

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

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/auth/sign-in",
}));

const isGithubOAuthAvailable = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    auth: {
      ...actual.auth,
      isGithubOAuthAvailable: (...args: unknown[]) =>
        isGithubOAuthAvailable(...args),
    },
  };
});

import SignInPage from "@/app/auth/sign-in/page";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("SignInPage — GitHub button visibility (P0-7)", () => {
  it("renders the GitHub button when isGithubOAuthAvailable() resolves true", async () => {
    isGithubOAuthAvailable.mockResolvedValueOnce(true);

    render(<SignInPage />);

    await waitFor(() =>
      expect(
        screen.queryByTestId("signin-github-button"),
      ).toBeInTheDocument(),
    );
    expect(screen.getByTestId("signin-github-button")).toHaveTextContent(
      /continue with github/i,
    );
  });

  it("hides the GitHub button when isGithubOAuthAvailable() resolves false", async () => {
    isGithubOAuthAvailable.mockResolvedValueOnce(false);

    render(<SignInPage />);

    // Give the effect a microtask to resolve, then assert absence.
    await waitFor(() => {
      // The probe has been called at least once.
      expect(isGithubOAuthAvailable).toHaveBeenCalled();
    });
    expect(
      screen.queryByTestId("signin-github-button"),
    ).not.toBeInTheDocument();
    // The email-link form remains visible regardless.
    expect(screen.getByLabelText(/^email$/i)).toBeInTheDocument();
  });

  it("hides the GitHub button when the probe rejects", async () => {
    isGithubOAuthAvailable.mockRejectedValueOnce(new Error("network"));

    render(<SignInPage />);

    await waitFor(() => {
      expect(isGithubOAuthAvailable).toHaveBeenCalled();
    });
    expect(
      screen.queryByTestId("signin-github-button"),
    ).not.toBeInTheDocument();
  });
});
