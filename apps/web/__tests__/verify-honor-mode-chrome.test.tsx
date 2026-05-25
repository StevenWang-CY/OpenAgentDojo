/**
 * B.3 — VerifyPageBody chrome branches on ``envelope.proctored``.
 *
 *   * ``proctored === true``  → "// verified report" eyebrow,
 *                                "Identity verified" subtitle.
 *   * ``proctored === false`` → "// honor mode attestation" eyebrow,
 *                                "Self-study attempt — not a verified
 *                                credential." subtitle.
 *
 * The contract matters because the verify URL is the credential surface
 * users share publicly — an honor-mode share that visually masquerades
 * as a verified credential would mislead reviewers.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { VerifyPageBody } from "@/components/verify/VerifyPageBody";
import type { VerifyEnvelope } from "@/lib/api";

vi.mock("@/lib/telemetry", () => ({
  track: vi.fn(),
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
  submission_id: "22222222-2222-2222-2222-222222222222",
  handle: "alex",
  display_name: null,
  mission_id: "auth-cookie-expiration",
  mission_title: "Expired Session Cookie Still Grants Access",
  mission_version: 1,
  rubric_version: "v1",
  total_score: 64,
  effective_max: 100,
  missed_failure_mode: false,
  score_cap_reason: null,
  proctored: false,
  attempt_index: 1,
  graded_at: "2026-05-23T18:42:11Z",
  canonical_url:
    "https://openagentdojo.app/verify/22222222-2222-2222-2222-222222222222",
  verification_hash: "deadbeef".repeat(8),
  verification_signature: "cafebabe".repeat(8),
};

describe("VerifyPageBody — honor-mode chrome (B.3)", () => {
  it("renders honor-mode chrome when envelope.proctored === false", () => {
    render(
      <VerifyPageBody envelope={{ ...baseEnvelope, proctored: false }} />,
    );

    // Eyebrow + subtitle copy.
    expect(
      screen.getByText(/honor mode attestation/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /self-study attempt — not a verified credential\./i,
      ),
    ).toBeInTheDocument();

    // The verified-mode copy MUST NOT appear.
    expect(screen.queryByText(/identity verified/i)).not.toBeInTheDocument();

    // Banner data attribute exposes the mode for downstream test
    // selectors / styling assertions.
    const banner = screen.getByTestId("verify-mode-banner");
    expect(banner).toHaveAttribute("data-proctored", "false");
  });

  it("renders verified chrome when envelope.proctored === true", () => {
    render(
      <VerifyPageBody envelope={{ ...baseEnvelope, proctored: true }} />,
    );

    expect(screen.getByText(/identity verified/i)).toBeInTheDocument();
    expect(screen.getByText(/\/\/ verified report/i)).toBeInTheDocument();
    expect(
      screen.queryByText(/honor mode attestation/i),
    ).not.toBeInTheDocument();

    const banner = screen.getByTestId("verify-mode-banner");
    expect(banner).toHaveAttribute("data-proctored", "true");
  });
});
