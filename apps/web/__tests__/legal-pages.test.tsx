import * as React from "react";
import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  __esModule: true,
  default: ({
    children,
    href,
    ...rest
  }: { children: React.ReactNode; href: string } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import TermsPage from "@/app/(marketing)/legal/terms/page";
import PrivacyPage from "@/app/(marketing)/legal/privacy/page";
import CookiesPage from "@/app/(marketing)/legal/cookies/page";

afterEach(() => cleanup());

describe("legal pages", () => {
  it("renders the Terms of service headline and a key section", () => {
    render(<TermsPage />);
    expect(
      screen.getByRole("heading", { level: 1, name: /terms of service/i }),
    ).toBeInTheDocument();
    // Content-checklist coverage: sandbox usage, prohibited workloads,
    // fair-use rate limits, no-warranty, IP retention.
    expect(
      screen.getByRole("heading", { level: 2, name: /prohibited workloads/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: /fair-use rate limits/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: /no warranty/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: /ip retention/i }),
    ).toBeInTheDocument();
  });

  it("renders the Privacy policy headline and every required section", () => {
    render(<PrivacyPage />);
    expect(
      screen.getByRole("heading", { level: 1, name: /privacy policy/i }),
    ).toBeInTheDocument();
    // P0-5 content checklist: what / why / how long / processors / rights /
    // contact. Match on stable phrasing.
    expect(
      screen.getByRole("heading", { level: 2, name: /what we collect/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: /why we collect/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: /how long we keep/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: /sub-processors/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: /your rights/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: /contact/i }),
    ).toBeInTheDocument();
  });

  it("renders the Cookies page with each tracked cookie name", () => {
    render(<CookiesPage />);
    expect(
      screen.getByRole("heading", { level: 1, name: /cookies and similar storage/i }),
    ).toBeInTheDocument();
    // The table is the substance of the page; assert all four listed cookies
    // appear at least once (some names also appear in the surrounding prose,
    // e.g. the "managing your choices" list, so we use getAllByText).
    expect(screen.getAllByText("arena_session").length).toBeGreaterThan(0);
    expect(screen.getAllByText("arena_csrf").length).toBeGreaterThan(0);
    expect(screen.getAllByText("consent_v").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/_posthog/).length).toBeGreaterThan(0);
  });
});
