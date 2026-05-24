"use client";

import * as React from "react";
import Link from "next/link";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { Switch } from "@/components/ui/Switch";
import { Button } from "@/components/ui/Button";
import { setConsentBulk, useConsent } from "@/lib/consent";
import { auth, ApiError } from "@/lib/api";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Fired after a successful "Save preferences". */
  onSaved?: () => void;
}

/**
 * Per-kind toggle dialog for the cookie banner (P0-5).
 *
 * Layout: one row per ``ConsentKind`` with a Switch on the right. Functional
 * cookies are non-toggleable (they're the auth + CSRF cookies — opting out
 * would brick the product), so their Switch is rendered disabled with helper
 * text. The dialog mirrors the live ``useConsent`` state so opening it after
 * a prior visit shows the user's saved choices, not the defaults.
 *
 * Why local draft state vs. live writes per toggle:
 *   - The user can flip several switches before committing; live writes
 *     would spam the server with intermediate states.
 *   - Cancel via Escape or the close button discards the draft, matching
 *     the user's mental model of a settings dialog.
 *
 * Accessibility: Radix Dialog already traps focus and binds Escape to close.
 * Each Switch is wired to its row label via ``aria-labelledby``. Note that
 * ``htmlFor`` would NOT work here because Radix Switch renders a ``<button>``
 * (not an ``<input>``); the label-for/input-id contract is input-only.
 */
export function CookieCustomizeDialog({ open, onOpenChange, onSaved }: Props) {
  const { state } = useConsent();
  const [analyticsDraft, setAnalyticsDraft] = React.useState<boolean>(false);
  const [marketingDraft, setMarketingDraft] = React.useState<boolean>(false);

  // When the dialog opens, seed the drafts from the live state so the
  // saved choices are visible (not the initial-render defaults).
  React.useEffect(() => {
    if (!open) return;
    setAnalyticsDraft(Boolean(state.analytics?.granted));
    setMarketingDraft(Boolean(state.marketing?.granted));
  }, [open, state.analytics, state.marketing]);

  const save = async () => {
    setConsentBulk({
      analytics: analyticsDraft,
      functional: true,
      marketing: marketingDraft,
    });
    onSaved?.();
    await Promise.all(
      (["analytics", "functional", "marketing"] as const).map(async (kind) => {
        const granted =
          kind === "functional"
            ? true
            : kind === "analytics"
            ? analyticsDraft
            : marketingDraft;
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
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Cookie preferences</DialogTitle>
          <DialogDescription>
            Choose which categories of cookies you allow. You can change this
            any time from Account → Privacy.
          </DialogDescription>
        </DialogHeader>

        <ul className="mt-2 divide-y divide-[var(--color-border)] rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)]">
          <Row
            labelId="cookie-row-functional-label"
            title="Functional"
            description="Required for sign-in and CSRF protection. Can't be turned off."
          >
            <Switch
              checked
              disabled
              aria-labelledby="cookie-row-functional-label"
              aria-label="Functional cookies are required and always on"
            />
          </Row>
          <Row
            labelId="cookie-row-analytics-label"
            title="Analytics"
            description="Anonymous product usage. Helps us spot dead-ends and broken flows."
          >
            <Switch
              checked={analyticsDraft}
              onCheckedChange={(value) => setAnalyticsDraft(Boolean(value))}
              aria-labelledby="cookie-row-analytics-label"
            />
          </Row>
          <Row
            labelId="cookie-row-marketing-label"
            title="Marketing"
            description="Reserved. We don't currently ship any marketing cookies."
          >
            <Switch
              checked={marketingDraft}
              onCheckedChange={(value) => setMarketingDraft(Boolean(value))}
              aria-labelledby="cookie-row-marketing-label"
            />
          </Row>
        </ul>

        <DialogFooter className="items-center">
          <Link
            href="/legal/cookies"
            className="mr-auto text-xs text-[var(--color-muted-foreground)] underline-offset-4 hover:underline"
          >
            Read the cookie policy
          </Link>
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button size="sm" onClick={save} aria-label="Save cookie preferences">
            Save preferences
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Row({
  labelId,
  title,
  description,
  children,
}: {
  /** ID applied to the row title span so the paired Switch can wire to it
   *  via ``aria-labelledby``. We don't use ``<label htmlFor>`` because Radix
   *  Switch renders a ``<button>`` and the for/id contract is input-only. */
  labelId: string;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <li className="flex items-start justify-between gap-4 px-4 py-3.5">
      <div className="min-w-0">
        <span
          id={labelId}
          className="block text-sm font-medium text-[var(--color-foreground)]"
        >
          {title}
        </span>
        <p className="mt-0.5 text-xs leading-relaxed text-[var(--color-muted-foreground)]">
          {description}
        </p>
      </div>
      <div className="pt-1">{children}</div>
    </li>
  );
}
