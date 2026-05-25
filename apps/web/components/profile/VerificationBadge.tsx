/**
 * P0-7 — VerificationBadge.
 *
 * Renders one of two chips next to the profile header:
 *
 *   * "verified · github · @login" — when ``github_verified_at`` is
 *     non-null. The chip is a real anchor pointing at the user's GitHub
 *     profile so consumers (recruiters, hiring managers) can independently
 *     sanity-check the identity in a single click.
 *   * "self-attested" — when ``github_verified_at`` is null. The tooltip
 *     spells out that the identity is not linked to a GitHub account so
 *     consumers can calibrate trust (the same handle could be claimed by
 *     anyone with that email).
 *
 * The chip is intentionally compact — it lives in the profile header
 * rather than a hero card, so the eye walks "@handle · verified" without
 * the chip dominating. We use a check-circle icon on the verified branch
 * and an info-circle on the self-attested branch.
 */

import * as React from "react";
import { BadgeCheck, Info } from "lucide-react";
import type { PublicProfile } from "@arena/shared-types";
import { formatDate } from "@/lib/format";

export interface VerificationBadgeProps {
  /** A small subset of ``PublicProfile``. We accept ``Pick<…>`` so the
   *  component can render against either the public profile payload or
   *  the authenticated ``/me`` payload without a wrapper. */
  profile: Pick<
    PublicProfile,
    "github_login" | "github_html_url" | "github_verified_at"
  >;
  /** Optional override class so callers can constrain the badge size when
   *  it lives inside a tight layout (e.g. the account panel). */
  className?: string;
}

export function VerificationBadge({
  profile,
  className,
}: VerificationBadgeProps) {
  // ``github_verified_at`` may be ``null`` (self-attested) or ``undefined``
  // (legacy fixture / older API surface). Treat both as the same
  // "self-attested" branch so the component is defensive against
  // pre-migration data shapes.
  const verifiedAt = profile.github_verified_at;
  const verified =
    verifiedAt !== null && verifiedAt !== undefined && verifiedAt !== "";
  if (verified) {
    const verifiedDate = formatDate(verifiedAt);
    const tooltip = `Identity verified via GitHub OAuth on ${verifiedDate}.`;
    const label = profile.github_login
      ? `verified · github · @${profile.github_login}`
      : "verified · github";
    const href = profile.github_html_url;
    const inner = (
      <>
        <BadgeCheck className="size-3.5" aria-hidden />
        <span className="font-mono text-[10.5px] uppercase tracking-[0.12em]">
          {label}
        </span>
      </>
    );
    const baseClass = [
      "inline-flex items-center gap-1.5 rounded-full",
      "border border-[oklch(from_var(--color-success)_l_c_h/0.45)]",
      "bg-[oklch(from_var(--color-success)_l_c_h/0.08)]",
      "px-2.5 py-1 text-[var(--color-success)]",
      className ?? "",
    ]
      .filter(Boolean)
      .join(" ");
    if (href) {
      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          title={tooltip}
          aria-label={tooltip}
          className={`${baseClass} hover:underline`}
          data-testid="verification-badge-verified"
        >
          {inner}
        </a>
      );
    }
    // Defensive: if the backend somehow stamped ``github_verified_at``
    // without ``github_html_url``, render a non-anchor chip so the badge
    // still surfaces.
    return (
      <span
        title={tooltip}
        aria-label={tooltip}
        className={baseClass}
        data-testid="verification-badge-verified"
      >
        {inner}
      </span>
    );
  }
  const tooltip =
    "Self-attested — not linked to a GitHub account. Sign in with GitHub to verify your identity.";
  const baseClass = [
    "inline-flex items-center gap-1.5 rounded-full",
    "border border-[var(--color-border)]",
    "bg-[var(--color-surface-elevated)]",
    "px-2.5 py-1 text-[var(--color-muted-foreground)]",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <span
      title={tooltip}
      aria-label={tooltip}
      className={baseClass}
      data-testid="verification-badge-self-attested"
    >
      <Info className="size-3.5" aria-hidden />
      <span className="font-mono text-[10.5px] uppercase tracking-[0.12em]">
        self-attested
      </span>
    </span>
  );
}
