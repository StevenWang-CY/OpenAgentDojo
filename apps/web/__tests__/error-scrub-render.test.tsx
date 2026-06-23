/**
 * P2 fix — the route-level error boundary must render the SCRUBBED message,
 * not the raw ``error.message``.
 *
 * ``app/error.tsx`` defined ``scrubError()`` (strips email + bearer-token-like
 * 40+ char alphanumeric runs) but only used it for the console log, then
 * painted the unscrubbed ``error.message`` straight into the DOM. A thrown
 * error carrying a leaked token or email therefore shipped that secret into
 * the rendered page. This suite locks the scrubbed text into the JSX.
 */
import * as React from "react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

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

import GlobalError from "@/app/error";

let warnSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  warnSpy.mockRestore();
  cleanup();
});

describe("GlobalError — scrubbed render", () => {
  it("redacts a 40+ char bearer-token-like substring before painting it", () => {
    const token = "A".repeat(48); // 48-char alphanum run → matches the scrubber
    const error = Object.assign(
      new Error(`fetch failed with Authorization: Bearer ${token}`),
      { digest: "abc123" },
    );

    render(<GlobalError error={error} reset={() => {}} />);

    const detail = screen.getByText(/fetch failed with/i);
    // The raw token must NOT appear in the DOM…
    expect(detail.textContent).not.toContain(token);
    // …it must be replaced by the scrubber's placeholder.
    expect(detail.textContent).toContain("[token]");
  });

  it("redacts an email address before painting it", () => {
    const error = new Error("validation failed for user ada@example.com");

    render(<GlobalError error={error} reset={() => {}} />);

    const detail = screen.getByText(/validation failed for user/i);
    expect(detail.textContent).not.toContain("ada@example.com");
    expect(detail.textContent).toContain("[email]");
  });

  it("logs the scrubbed (not raw) detail to console.warn exactly once", () => {
    const token = "B".repeat(50);
    const error = new Error(`boom ${token}`);

    render(<GlobalError error={error} reset={() => {}} />);

    expect(warnSpy).toHaveBeenCalledTimes(1);
    const logged = warnSpy.mock.calls[0]?.join(" ") ?? "";
    expect(logged).not.toContain(token);
    expect(logged).toContain("[token]");
  });
});
