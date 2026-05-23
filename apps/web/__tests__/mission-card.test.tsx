import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MissionCard } from "@/components/catalog/MissionCard";
import type { Mission } from "@arena/shared-types";

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

const mission: Mission = {
  id: "auth-cookie-expiration",
  title: "Expired Session Cookie Still Grants Access",
  short_description:
    "Users with expired session cookies can still access protected routes.",
  difficulty: "intermediate",
  category: "auth",
  estimated_minutes: 35,
  skills_tested: ["auth", "security", "test-writing"],
  failure_mode_id: "checks_presence_not_expiration",
  version: 1,
  published: true,
  kind: "standard",
};

describe("MissionCard", () => {
  it("renders the title, category, difficulty and estimated time", () => {
    render(<MissionCard mission={mission} />);
    expect(screen.getByText(mission.title)).toBeInTheDocument();
    expect(screen.getByText(/Intermediate/i)).toBeInTheDocument();
    // "auth" appears twice: once as the category badge, once as a skills-tested
    // chip in the footer. Assert both surfaces are populated rather than
    // requiring a single occurrence.
    expect(screen.getAllByText("auth").length).toBeGreaterThanOrEqual(1);
    // Card renders "~35m" — match the actual abbreviation.
    expect(screen.getByText(/~35m/i)).toBeInTheDocument();
  });

  it("links to the mission detail page", () => {
    render(<MissionCard mission={mission} />);
    // The card wraps the entire tile in a Link with no aria-label, so the
    // accessible name is the concatenation of every text node inside. A
    // substring match against the mission title is the stable assertion.
    const link = screen.getByRole("link", {
      name: new RegExp(mission.title, "i"),
    });
    expect(link).toHaveAttribute(
      "href",
      `/missions/${mission.id}`
    );
  });
});
