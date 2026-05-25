/**
 * B.2 — Sign-in page threads ``?next=…`` into the magic-link request body.
 *
 * The post-redeem hop is owned by the backend, but the FE has to surface
 * the intent in the request body so the magic-link callback knows where
 * to land. This regression test pins the contract: when the page is
 * rendered with ``?next=/report/abc``, ``auth.sendMagicLink`` MUST be
 * called with ``{email, next: "/report/abc"}``.
 */

import * as React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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
  // The page reads ``next`` from this URLSearchParams; mount-time mock
  // returns a fixed instance so the assertion is deterministic.
  useSearchParams: () => new URLSearchParams("next=/report/abc"),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/auth/sign-in",
}));

const sendMagicLink = vi.fn();
const isGithubOAuthAvailable = vi.fn().mockResolvedValue(false);
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    auth: {
      ...actual.auth,
      sendMagicLink: (...args: unknown[]) => sendMagicLink(...args),
      isGithubOAuthAvailable: (...args: unknown[]) =>
        isGithubOAuthAvailable(...args),
    },
  };
});

import SignInPage from "@/app/auth/sign-in/page";

beforeEach(() => {
  vi.clearAllMocks();
  sendMagicLink.mockResolvedValue(undefined);
});

describe("SignInPage — ?next= threading (B.2)", () => {
  it("passes the sanitised ?next= path through to auth.sendMagicLink", async () => {
    render(<SignInPage />);

    fireEvent.change(screen.getByLabelText(/^email$/i), {
      target: { value: "alice@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /email me/i }));

    await waitFor(() => expect(sendMagicLink).toHaveBeenCalledTimes(1));
    expect(sendMagicLink).toHaveBeenCalledWith({
      email: "alice@example.com",
      next: "/report/abc",
    });
  });
});
