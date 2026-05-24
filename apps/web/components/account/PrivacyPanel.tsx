"use client";

/**
 * P0-6 — Privacy panel.
 *
 * Consumer of the consent library (``apps/web/lib/consent.ts``) owned by the
 * P0-5 agent. This panel does NOT modify the lib — it just renders one
 * ``Switch`` per consent kind plus a link to the full Privacy + Cookies
 * policies.
 *
 * Per ADR / P0_DESIGN §P0-5:
 *   - ``functional`` cookies cannot be turned off (they're load-bearing for
 *     the product to work) — the switch is disabled with a helper line.
 *   - Toggling a kind writes localStorage + (when signed in) appends a row
 *     to the server-side ``user_consents`` audit table. The hook swallows
 *     401s silently for the anonymous path.
 */

import * as React from "react";
import Link from "next/link";
import type { ConsentKind, ConsentState } from "@arena/shared-types";
import { Switch } from "@/components/ui/Switch";
import { useConsent } from "@/lib/consent";
import { SectionLabel } from "./AccountView";

interface KindMeta {
  kind: ConsentKind;
  label: string;
  description: string;
  /** ``functional`` is non-toggleable per ADR 0011 — without it the cookie
   *  banner itself can't render. The Switch stays for visual consistency
   *  but is locked on with an explanatory helper. */
  locked?: boolean;
}

const KINDS: KindMeta[] = [
  {
    kind: "analytics",
    label: "Anonymous product analytics",
    description:
      "Helps us see which surfaces are confusing (e.g. coachmark drop-off, mission popularity). No content from your sessions, prompts, or code is ever sent.",
  },
  {
    kind: "functional",
    label: "Essential functional cookies",
    description:
      "Required for sign-in, CSRF, and remembering theme. Can't be disabled without breaking the product.",
    locked: true,
  },
  {
    kind: "marketing",
    label: "Marketing emails",
    description:
      "Occasional product updates, new mission drops, and changelogs. Opt-in. We don't sell your address.",
  },
];

function checkedFor(state: ConsentState, kind: ConsentKind): boolean {
  const record = state[kind];
  if (kind === "functional") return true;
  return record?.granted === true;
}

export function PrivacyPanel() {
  const { state, setKind } = useConsent();
  const [pendingKind, setPendingKind] = React.useState<ConsentKind | null>(null);

  async function onChange(kind: ConsentKind, granted: boolean) {
    if (kind === "functional") return; // locked
    setPendingKind(kind);
    try {
      await setKind(kind, granted);
    } finally {
      setPendingKind(null);
    }
  }

  return (
    <section aria-labelledby="privacy-heading" className="space-y-6">
      <header>
        <SectionLabel>privacy</SectionLabel>
        <h2 id="privacy-heading" className="mt-1 text-lg font-semibold">
          Privacy &amp; consent
        </h2>
        <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
          You control what we track. Changes apply immediately on this
          device; if you&rsquo;re signed in we also append an audit row so
          the choice persists across browsers.
        </p>
      </header>

      <ul className="grid gap-4" data-testid="consent-list">
        {KINDS.map((meta) => {
          const checked = checkedFor(state, meta.kind);
          const isPending = pendingKind === meta.kind;
          return (
            <li
              key={meta.kind}
              className="flex items-start justify-between gap-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4"
              data-testid={`consent-row-${meta.kind}`}
            >
              <div className="flex-1">
                <p className="text-sm font-medium">{meta.label}</p>
                <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">
                  {meta.description}
                </p>
              </div>
              <Switch
                checked={checked}
                onCheckedChange={(next) => void onChange(meta.kind, next)}
                disabled={meta.locked || isPending}
                aria-label={`Toggle ${meta.label}`}
                data-testid={`consent-toggle-${meta.kind}`}
              />
            </li>
          );
        })}
      </ul>

      <footer className="flex flex-wrap items-center gap-4 border-t border-[var(--color-border)] pt-4 text-xs text-[var(--color-muted-foreground)]">
        <Link
          href="/legal/privacy"
          className="underline underline-offset-2 hover:text-[var(--color-foreground)]"
        >
          Privacy policy
        </Link>
        <Link
          href="/legal/cookies"
          className="underline underline-offset-2 hover:text-[var(--color-foreground)]"
        >
          Cookie policy
        </Link>
      </footer>
    </section>
  );
}
