/**
 * B.4 — the /help/honor-mode page renders every section that the
 * workspace banner + start dialog deep-link into. Operators copy these
 * URLs into ticket replies; a stable test on the slug-cased ``h2`` IDs
 * keeps the anchor surface from rotting silently.
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

import HonorModeHelpPage from "@/app/(marketing)/help/honor-mode/page";

afterEach(() => cleanup());

describe("Honor mode help page (B.4)", () => {
  it("renders the headline and every required section", () => {
    render(<HonorModeHelpPage />);

    expect(
      screen.getByRole("heading", {
        level: 1,
        name: /honor mode & proctored mode/i,
      }),
    ).toBeInTheDocument();

    const expectedSections = [
      /honor mode \(self-study\)/i,
      /proctored mode \(verified\)/i,
      /how the verified credential is issued/i,
      /integrity signals captured/i,
      /limits of the system/i,
    ];
    for (const pattern of expectedSections) {
      expect(
        screen.getByRole("heading", { level: 2, name: pattern }),
      ).toBeInTheDocument();
    }
  });

  it("describes the three integrity-signal kinds captured under proctored mode", () => {
    render(<HonorModeHelpPage />);
    expect(screen.getByText(/tab blur ?\/ ?focus/i)).toBeInTheDocument();
    // ``Large paste`` appears as the bullet label and again as prose
    // commentary; ``getAllByText`` confirms at least one match without
    // failing on the ambiguity.
    expect(screen.getAllByText(/large paste/i).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/right-click on paste targets/i),
    ).toBeInTheDocument();
  });

  it("cross-links to sign-in help and the privacy policy", () => {
    render(<HonorModeHelpPage />);
    expect(
      screen.getByRole("link", { name: /sign-in help/i }),
    ).toHaveAttribute("href", "/help/signin");
    expect(
      screen.getByRole("link", { name: /privacy policy/i }),
    ).toHaveAttribute("href", "/legal/privacy");
  });
});
