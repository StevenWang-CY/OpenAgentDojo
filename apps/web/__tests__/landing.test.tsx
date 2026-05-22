import * as React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import LandingPage from "@/app/(marketing)/page";

function renderWithClient(node: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>
  );
}

// Next.js's `Link` is rendered as a plain anchor in jsdom by default.
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

describe("landing page", () => {
  it("renders the redesigned hero headline", () => {
    renderWithClient(<LandingPage />);
    // The headline mixes a curly apostrophe and a <br> so we match the
    // unambiguous opening phrase rather than the full string.
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: /Patches that look right/i,
      })
    ).toBeInTheDocument();
  });

  it("links to the missions catalog", () => {
    renderWithClient(<LandingPage />);
    const cta = screen.getByRole("link", { name: /Browse missions/i });
    expect(cta).toHaveAttribute("href", "/missions");
  });

  it("describes what the platform grades", () => {
    renderWithClient(<LandingPage />);
    // The subhead is broken across an inline <em> so we walk up and
    // match on the rolled-up text. `getAllByText` because every ancestor
    // also satisfies the predicate; we only need at least one hit.
    const matches = screen.getAllByText((_, node) => {
      if (!node) return false;
      const text = node.textContent ?? "";
      return /grades the\s+process\s+of\s+supervision/i.test(text);
    });
    expect(matches.length).toBeGreaterThan(0);
  });
});
