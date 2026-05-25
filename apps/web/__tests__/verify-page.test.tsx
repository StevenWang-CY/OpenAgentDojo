/**
 * P0-11 — VerifyPageBody render contract.
 *
 * Asserts:
 *  - Headline score + denominator render.
 *  - Mission title + handle + attempt index render.
 *  - Verification hash + signature are visible as code rows.
 *  - The "gave_up" cap chip renders only when score_cap_reason === "gave_up".
 *  - The page mounts the report_verified telemetry event once.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { VerifyPageBody } from "@/components/verify/VerifyPageBody";
import type { VerifyEnvelope } from "@/lib/api";

const trackMock = vi.fn();
vi.mock("@/lib/telemetry", () => ({
  track: (...args: unknown[]) => trackMock(...args),
}));
vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    message: vi.fn(),
  },
}));

const baseEnvelope: VerifyEnvelope = {
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

beforeEach(() => {
  trackMock.mockClear();
});

afterEach(() => {
  trackMock.mockClear();
});

describe("VerifyPageBody", () => {
  it("renders the headline score + mission + handle + attempt", () => {
    render(<VerifyPageBody envelope={baseEnvelope} />);
    expect(screen.getByText("78")).toBeInTheDocument();
    expect(
      screen.getByText(
        baseEnvelope.mission_title,
        { exact: false },
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/@jane/)).toBeInTheDocument();
    expect(screen.getByText(/attempt 2/i)).toBeInTheDocument();
  });

  it("surfaces the failure-mode-identified affirmation when not missed", () => {
    render(<VerifyPageBody envelope={baseEnvelope} />);
    expect(
      screen.getByText(/failure mode identified/i),
    ).toBeInTheDocument();
  });

  it("surfaces 'missed' when missed_failure_mode is true", () => {
    render(
      <VerifyPageBody
        envelope={{ ...baseEnvelope, missed_failure_mode: true }}
      />,
    );
    expect(
      screen.getByText(/missed the mission's failure mode/i),
    ).toBeInTheDocument();
  });

  it("renders the gave_up chip only when capped", () => {
    const { rerender } = render(<VerifyPageBody envelope={baseEnvelope} />);
    expect(screen.queryByText(/gave up/i)).not.toBeInTheDocument();
    rerender(
      <VerifyPageBody
        envelope={{ ...baseEnvelope, score_cap_reason: "gave_up" }}
      />,
    );
    expect(
      screen.getByText(/score capped at 50 \/ 100/i),
    ).toBeInTheDocument();
  });

  it("fires the report_verified telemetry event once on mount", () => {
    render(<VerifyPageBody envelope={baseEnvelope} />);
    expect(trackMock).toHaveBeenCalledTimes(1);
    expect(trackMock).toHaveBeenCalledWith(
      "report_verified",
      expect.objectContaining({
        submission_id: baseEnvelope.submission_id,
      }),
    );
  });
});
