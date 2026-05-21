import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LoginForm } from "../../LoginForm";

describe("LoginForm", () => {
  it("renders the user-id input and submit button", () => {
    const onSignedIn = vi.fn();
    render(<LoginForm onSignedIn={onSignedIn} />);
    expect(screen.getByLabelText(/user id/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
  });

  it("disables the submit button while the user id is empty", () => {
    const onSignedIn = vi.fn();
    render(<LoginForm onSignedIn={onSignedIn} />);
    expect(screen.getByRole("button", { name: /sign in/i })).toBeDisabled();
  });
});
