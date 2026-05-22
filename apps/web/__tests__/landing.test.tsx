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
  it("renders the Hello, OpenAgentDojo hero", () => {
    renderWithClient(<LandingPage />);
    expect(
      screen.getByRole("heading", { level: 1, name: /Hello, OpenAgentDojo/i })
    ).toBeInTheDocument();
  });

  it("links to the missions catalog", () => {
    renderWithClient(<LandingPage />);
    const cta = screen.getByRole("link", { name: /Browse Missions/i });
    expect(cta).toHaveAttribute("href", "/missions");
  });

  it("describes what the platform does", () => {
    renderWithClient(<LandingPage />);
    expect(
      screen.getByText(/supervise coding agents/i)
    ).toBeInTheDocument();
  });
});
