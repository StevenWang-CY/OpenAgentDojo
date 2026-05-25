/**
 * P0-10 — the /help/signin page renders every section the deliverability
 * runbook references. The headings double as anchor targets for support
 * deep-links (``/help/signin#expired-link`` etc.) so a stable test
 * around the slug-cased IDs prevents silent decay.
 */

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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

import SignInHelpPage from "@/app/(marketing)/help/signin/page";

afterEach(() => cleanup());

describe("Sign-in help page (P0-10)", () => {
  it("renders the headline and every required section", () => {
    render(<SignInHelpPage />);

    // Headline.
    expect(
      screen.getByRole("heading", { level: 1, name: /sign-in help/i }),
    ).toBeInTheDocument();

    // Each FAQ section the runbook directs operators to.
    const expectedSections = [
      /didn.?t get the email/i,
      /corporate mail server/i,
      /outlook 365|microsoft 365/i,
      /link expired/i,
      /can.?t access my email/i,
      /still stuck/i,
    ];
    for (const pattern of expectedSections) {
      expect(
        screen.getByRole("heading", { level: 2, name: pattern }),
      ).toBeInTheDocument();
    }
  });

  it("links back to the sign-in flow and to the privacy policy", () => {
    render(<SignInHelpPage />);
    expect(
      screen.getAllByRole("link", { name: /sign in/i })[0],
    ).toHaveAttribute("href", "/auth/sign-in");
    expect(
      screen.getByRole("link", { name: /privacy policy/i }),
    ).toHaveAttribute("href", "/legal/privacy");
  });

  it("mentions the GitHub OAuth fallback in the email-locked-out section", () => {
    render(<SignInHelpPage />);
    expect(screen.getAllByText(/github oauth/i)[0]).toBeInTheDocument();
  });
});
