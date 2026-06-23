/**
 * P2 fix — the verify page Copy buttons must report copy outcomes HONESTLY.
 *
 * The old handler did ``void navigator.clipboard?.writeText(value);
 * toast.success(...)`` — it no-op'd when ``navigator.clipboard`` was
 * undefined (insecure origin / older browser), swallowed any rejection
 * (permission denied), and ALWAYS claimed success. The fix awaits the write
 * and toasts success only on resolve; absence or rejection toasts an error.
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
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { VerifyEnvelope } from "@/lib/api";

vi.mock("@/lib/telemetry", () => ({ track: vi.fn() }));

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
    message: vi.fn(),
  },
}));

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

import { VerifyPageBody } from "@/components/verify/VerifyPageBody";

const envelope: VerifyEnvelope = {
  schema_version: 1,
  submission_id: "11111111-1111-1111-1111-111111111111",
  handle: "jane",
  display_name: "Jane Doe",
  mission_id: "auth-cookie-expiration",
  mission_title: "Expired Session Cookie Still Grants Access",
  mission_version: 1,
  rubric_version: "v1",
  total_score: 78,
  effective_max: 100,
  missed_failure_mode: false,
  score_cap_reason: null,
  proctored: false,
  attempt_index: 2,
  graded_at: "2026-05-23T18:42:11Z",
  canonical_url:
    "https://openagentdojo.app/verify/11111111-1111-1111-1111-111111111111",
  verification_hash:
    "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
  verification_signature:
    "0011223344556677889900112233445566778899001122334455667788990011",
};

const originalClipboard = (
  globalThis.navigator as Navigator & { clipboard?: Clipboard }
).clipboard;

function setClipboard(value: Clipboard | undefined): void {
  Object.defineProperty(globalThis.navigator, "clipboard", {
    configurable: true,
    value,
  });
}

beforeEach(() => {
  toastSuccess.mockClear();
  toastError.mockClear();
});

afterEach(() => {
  cleanup();
  setClipboard(originalClipboard);
});

describe("VerifyPageBody — Copy button honesty", () => {
  it("toasts SUCCESS only after the clipboard write resolves", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard({ writeText } as unknown as Clipboard);

    render(<VerifyPageBody envelope={envelope} />);
    fireEvent.click(
      screen.getByRole("button", { name: /copy verification_hash/i }),
    );

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText).toHaveBeenCalledWith(envelope.verification_hash);
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledTimes(1));
    expect(toastError).not.toHaveBeenCalled();
  });

  it("toasts ERROR (not success) when navigator.clipboard is unavailable", async () => {
    setClipboard(undefined);

    render(<VerifyPageBody envelope={envelope} />);
    fireEvent.click(
      screen.getByRole("button", { name: /copy verification_hash/i }),
    );

    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("toasts ERROR (not success) when the clipboard write rejects", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    setClipboard({ writeText } as unknown as Clipboard);

    render(<VerifyPageBody envelope={envelope} />);
    fireEvent.click(
      screen.getByRole("button", { name: /copy signature/i }),
    );

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
    expect(toastSuccess).not.toHaveBeenCalled();
  });
});
