import * as React from "react";
import { SectionLabel } from "@/components/ui/SectionLabel";

/**
 * Layout chrome for ``/legal/*`` pages (P0-5). Each page passes a slug
 * (rendered as the leading ``// foo`` mono caption) plus the headline and
 * body. The shell renders the "Effective date" notice so individual pages
 * stay focused on copy.
 *
 * Why a component instead of a route-level layout: each legal page has its
 * own title/headline, and the route-level ``layout.tsx`` would have to
 * receive that data through ``params``. A plain wrapper component keeps the
 * relationship explicit.
 */
export interface LegalShellProps {
  /** Mono caption — appears as ``// {slug}`` above the headline. */
  slug: string;
  /** Page headline rendered as ``<h1>``. */
  title: string;
  children: React.ReactNode;
  /** Override the effective date if a future revision lands. Defaults to the
   *  current policy launch (2026-05-24, matching LATEST_CONSENT_VERSION = 1).
   */
  effectiveDate?: string;
  /** Mirror of the policy version number. Bump in lockstep with
   *  ``LATEST_CONSENT_VERSION`` and the backend's
   *  ``settings.consent_policy_version``. */
  version?: number;
}

export function LegalShell({
  slug,
  title,
  children,
  effectiveDate = "2026-05-24",
  version = 1,
}: LegalShellProps) {
  return (
    <article className="mx-auto max-w-3xl px-6 py-16">
      <SectionLabel>{slug}</SectionLabel>
      <h1 className="mt-4 text-4xl font-semibold leading-[1.1] tracking-[-0.02em] sm:text-[44px]">
        {title}
      </h1>
      <p className="mt-3 text-sm text-[var(--color-muted-foreground)]">
        Effective date: {effectiveDate} · Version {version}
      </p>
      <div className="legal-prose mt-10">{children}</div>
    </article>
  );
}
