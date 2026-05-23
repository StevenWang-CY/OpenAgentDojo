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

const sendMagicLink = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    auth: { ...actual.auth, sendMagicLink: (...args: unknown[]) => sendMagicLink(...args) },
  };
});

import SignInPage from "@/app/auth/sign-in/page";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("sign-in error handling", () => {
  it("does not flip to the 'check your email' success state on network failure", async () => {
    const { ApiError } = await import("@/lib/api");
    sendMagicLink.mockRejectedValueOnce(
      new ApiError("Network error contacting /auth/magic-link", 0, null)
    );

    render(<SignInPage />);

    fireEvent.change(screen.getByLabelText(/^email$/i), {
      target: { value: "alice@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /email me/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/couldn't reach the api/i)
    );
    // The success state would render this string — confirm we do not show it.
    expect(screen.queryByText(/Check your inbox\./i)).not.toBeInTheDocument();
  });

  it("shows the success state on a real 204 response", async () => {
    sendMagicLink.mockResolvedValueOnce(undefined);

    render(<SignInPage />);

    fireEvent.change(screen.getByLabelText(/^email$/i), {
      target: { value: "alice@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /email me/i }));

    await waitFor(() =>
      expect(screen.getByText(/Check your inbox\./i)).toBeInTheDocument()
    );
  });

  it.each([
    { label: "empty", value: "" },
    { label: "whitespace-only", value: "   " },
    { label: "missing @", value: "alice.example.com" },
    { label: "missing TLD", value: "alice@example" },
    { label: "missing local part", value: "@example.com" },
  ])("rejects $label input client-side without calling the API", async ({ value }) => {
    render(<SignInPage />);

    fireEvent.change(screen.getByLabelText(/^email$/i), {
      target: { value },
    });
    fireEvent.click(screen.getByRole("button", { name: /email me/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        /please enter a valid email address/i
      )
    );
    expect(sendMagicLink).not.toHaveBeenCalled();
  });
});
