/**
 * P1 audit fix — Roadmap page renders a friendly, URL-free message when
 * the backend fetch fails.
 *
 * Previously the rendered DOM included
 *   ``// roadmap fetch failed: HTTP 500 from http://backend.internal/...``
 * which leaked backend hostnames to anonymous visitors. The new
 * behaviour:
 *
 *   - DOM shows ``// roadmap is offline — try the GitHub repo for the
 *     latest`` and nothing else about the failure.
 *   - The reason string lands in ``console.error`` for operators.
 */
import * as React from "react";
import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

// Force a known apiBaseUrl so we can assert it does NOT leak into the
// rendered DOM. (The lib/env.ts module reads from process.env at import
// time, so we set it before importing the page below.)
const SECRET_API_URL = "http://backend.internal.example.com:8000";
process.env.NEXT_PUBLIC_API_BASE_URL = SECRET_API_URL;

import RoadmapPage from "@/app/(marketing)/roadmap/page";

let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  consoleErrorSpy.mockRestore();
  vi.unstubAllGlobals();
});

describe("Roadmap page error surface (P1 audit)", () => {
  it("renders a friendly, URL-free message when the backend returns 500", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response("upstream gateway error", {
          status: 500,
          statusText: "Internal Server Error",
        }),
      ),
    );

    const ui = await RoadmapPage();
    render(ui);

    const notice = screen.getByTestId("roadmap-offline-notice");
    expect(notice).toBeInTheDocument();
    expect(notice).toHaveTextContent(/roadmap is offline/i);
    expect(notice).toHaveTextContent(/github repo/i);

    // No backend host, no status code, no internal path in the DOM.
    const dom = document.body.innerHTML;
    expect(dom).not.toContain(SECRET_API_URL);
    expect(dom).not.toContain("backend.internal");
    expect(dom).not.toMatch(/HTTP 500/);
    expect(dom).not.toMatch(/upstream gateway/i);

    // Operator signal: the reason still lands in console.error.
    expect(consoleErrorSpy).toHaveBeenCalled();
  });

  it("renders a friendly, URL-free message when fetch itself throws", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error(`ECONNREFUSED ${SECRET_API_URL}`);
      }),
    );

    const ui = await RoadmapPage();
    render(ui);

    const notice = screen.getByTestId("roadmap-offline-notice");
    expect(notice).toHaveTextContent(/roadmap is offline/i);

    const dom = document.body.innerHTML;
    expect(dom).not.toContain(SECRET_API_URL);
    expect(dom).not.toContain("ECONNREFUSED");

    expect(consoleErrorSpy).toHaveBeenCalled();
  });

  it("renders the upcoming missions list on a successful fetch (sanity)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify([
              {
                id: "go-request-deadline-stripped",
                title: "Request Deadline Stripped on Refactor",
                short_description: "go placeholder",
                language: "go",
                difficulty: "intermediate",
                category: "debugging",
                estimated_minutes: 0,
                failure_mode_id: "",
                skills_tested: [],
                version: 1,
                published: false,
                kind: "standard",
                repo_pack_id: null,
                tags: [],
                status: "coming_soon",
                target_release_date: "2026-07-01",
              },
            ]),
            {
              status: 200,
              headers: { "content-type": "application/json" },
            },
          ),
      ),
    );

    const ui = await RoadmapPage();
    render(ui);

    expect(screen.queryByTestId("roadmap-offline-notice")).toBeNull();
    expect(
      screen.getByText(/Request Deadline Stripped on Refactor/i),
    ).toBeInTheDocument();
  });

  it("renders the mission-authoring scaffold note in the footer", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify([]), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
      ),
    );

    const ui = await RoadmapPage();
    render(ui);

    const note = screen.getByTestId("roadmap-authoring-note");
    expect(note).toHaveTextContent(/want to author a mission/i);
    expect(note).toHaveTextContent("scripts/mission-template/");
  });
});
