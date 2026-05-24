"use client";

import * as React from "react";
import Link from "next/link";
import { Button } from "@/components/ui/Button";
import {
  applyEssentialOnlyDefault,
  bannerShouldShow,
  setConsentBulk,
  useConsent,
} from "@/lib/consent";
import { auth, ApiError } from "@/lib/api";
import { CookieCustomizeDialog } from "./CookieCustomizeDialog";

/**
 * First-visit cookie banner (P0-5). Sticks to the bottom of the viewport
 * and never auto-dismisses — the user must pick "Accept all", "Essential
 * only", or "Customize". This is deliberate: a click-to-close affordance
 * would let users dodge the consent choice entirely, which defeats the
 * audit trail.
 *
 * Rendering rules:
 *   1. The banner reads ``bannerShouldShow()`` synchronously after hydration
 *      so first paint never flashes a banner that's about to disappear.
 *   2. It listens to the {@link useConsent} hook so opt-in actions from
 *      anywhere (e.g. the in-app /account/privacy page) hide it without a
 *      reload.
 *   3. Each "Accept" path writes to localStorage AND best-effort posts to
 *      ``/auth/me/consent`` for signed-in users; the wrapper swallows the 401
 *      that anonymous users get.
 *
 * Accessibility:
 *   - ``role="region"`` + ``aria-label`` so screen readers announce it as a
 *     landmark instead of inline text.
 *   - The action buttons are real ``<button>``s with explicit labels — no
 *     ``aria-hidden`` icon-only patterns.
 */
export function CookieConsentBanner() {
  const [mounted, setMounted] = React.useState(false);
  const [shouldShow, setShouldShow] = React.useState(false);
  const [customizeOpen, setCustomizeOpen] = React.useState(false);
  const { state } = useConsent();

  React.useEffect(() => {
    setMounted(true);
    setShouldShow(bannerShouldShow());
  }, []);

  // After ``useConsent`` reports any record, the banner can hide. We
  // re-check on each state change so the in-app privacy tab can close the
  // banner remotely (cross-tab open while the user accepts in /account).
  React.useEffect(() => {
    if (!mounted) return;
    const stillEmpty =
      state.analytics === null &&
      state.functional === null &&
      state.marketing === null;
    setShouldShow(stillEmpty && bannerShouldShow());
  }, [state, mounted]);

  // Pre-hydration we render nothing; once mounted we may still render
  // nothing if the user has already opted in this browser. Both paths
  // avoid hydration mismatches because the server sends an empty <></> too.
  if (!mounted || !shouldShow) return null;

  const acceptAll = async () => {
    setConsentBulk({ analytics: true, functional: true, marketing: true });
    setShouldShow(false);
    await postBulkToServer({ analytics: true, functional: true, marketing: true });
  };

  const essentialOnly = async () => {
    applyEssentialOnlyDefault();
    setShouldShow(false);
    await postBulkToServer({
      analytics: false,
      functional: true,
      marketing: false,
    });
  };

  return (
    <>
      <div
        role="region"
        aria-label="Cookie consent"
        className="fixed inset-x-3 bottom-3 z-50 mx-auto max-w-[720px] rounded-2xl border border-[var(--color-border)] bg-[oklch(from_var(--color-surface)_l_c_h/0.92)] p-5 shadow-elevated backdrop-blur-md md:inset-x-auto md:bottom-6 md:left-1/2 md:-translate-x-1/2"
      >
        <p className="mb-1.5 font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
          {"// "}cookies
        </p>
        <p className="text-sm leading-relaxed text-[var(--color-foreground)]">
          We use essential cookies to keep you signed in and to protect you
          against CSRF. Optional analytics cookies help us improve the
          product. You can change this any time from{" "}
          <Link
            href="/account/privacy"
            className="font-medium text-[var(--color-primary)] underline-offset-4 hover:underline"
          >
            Account → Privacy
          </Link>{" "}
          — full details in our{" "}
          <Link
            href="/legal/cookies"
            className="font-medium text-[var(--color-primary)] underline-offset-4 hover:underline"
          >
            cookie policy
          </Link>
          .
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={acceptAll} aria-label="Accept all cookies">
            Accept all
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={essentialOnly}
            aria-label="Accept essential cookies only"
          >
            Essential only
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setCustomizeOpen(true)}
            aria-label="Customize cookie preferences"
          >
            Customize
          </Button>
        </div>
      </div>

      <CookieCustomizeDialog
        open={customizeOpen}
        onOpenChange={setCustomizeOpen}
        onSaved={() => {
          setCustomizeOpen(false);
          setShouldShow(false);
        }}
      />
    </>
  );
}

/**
 * Best-effort bulk POST to ``/auth/me/consent``. Each kind is one row in the
 * server audit trail; the banner UI never blocks on these calls because
 * localStorage is already the user's authoritative choice.
 *
 * On 401 (anonymous user) every call no-ops silently. Any other failure is
 * logged and ignored — the next consent change will retry.
 */
async function postBulkToServer(
  updates: Partial<Record<"analytics" | "functional" | "marketing", boolean>>,
): Promise<void> {
  const entries = Object.entries(updates) as ["analytics" | "functional" | "marketing", boolean][];
  await Promise.all(
    entries.map(async ([kind, granted]) => {
      try {
        await auth.setConsentRecord({ kind, granted });
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 401) {
          if (typeof console !== "undefined") {
            console.warn("[consent] server sync failed", error);
          }
        }
      }
    }),
  );
}
