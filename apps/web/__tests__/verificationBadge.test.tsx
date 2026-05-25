/**
 * P0-7 — VerificationBadge tests.
 *
 * Asserts the two rendering branches:
 *   - ``github_verified_at`` non-null → "verified · github · @login" chip
 *     wired as an anchor pointing at ``github_html_url``.
 *   - ``github_verified_at`` null → "self-attested" chip without a link.
 */

import * as React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { VerificationBadge } from "@/components/profile/VerificationBadge";

describe("VerificationBadge", () => {
  it("renders the verified chip linking to GitHub when github_verified_at is set", () => {
    render(
      <VerificationBadge
        profile={{
          github_login: "octocat",
          github_html_url: "https://github.com/octocat",
          github_verified_at: "2026-04-12T00:00:00Z",
        }}
      />,
    );
    const chip = screen.getByTestId("verification-badge-verified");
    expect(chip.tagName.toLowerCase()).toBe("a");
    expect(chip).toHaveAttribute("href", "https://github.com/octocat");
    expect(chip).toHaveAttribute("target", "_blank");
    expect(chip).toHaveAttribute("rel", "noopener noreferrer");
    expect(chip.textContent).toMatch(/verified/i);
    expect(chip.textContent).toMatch(/github/i);
    expect(chip.textContent).toMatch(/@octocat/i);
  });

  it("renders the self-attested chip when github_verified_at is null", () => {
    render(
      <VerificationBadge
        profile={{
          github_login: null,
          github_html_url: null,
          github_verified_at: null,
        }}
      />,
    );
    const chip = screen.getByTestId("verification-badge-self-attested");
    expect(chip.tagName.toLowerCase()).not.toBe("a");
    expect(chip.textContent).toMatch(/self-attested/i);
    expect(
      screen.queryByTestId("verification-badge-verified"),
    ).not.toBeInTheDocument();
  });

  it("falls back to a non-anchor chip when verified but html_url is missing", () => {
    render(
      <VerificationBadge
        profile={{
          github_login: "octocat",
          github_html_url: null,
          github_verified_at: "2026-04-12T00:00:00Z",
        }}
      />,
    );
    const chip = screen.getByTestId("verification-badge-verified");
    expect(chip.tagName.toLowerCase()).not.toBe("a");
    expect(chip.textContent).toMatch(/verified/i);
  });
});
