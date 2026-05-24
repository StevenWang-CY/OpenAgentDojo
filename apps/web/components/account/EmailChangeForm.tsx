"use client";

/**
 * P0-6 — Email change form (step 1 of the two-step magic-link flow).
 *
 * On submit, POSTs ``/auth/me/email/change`` which sets
 * ``users.pending_email`` and emails a one-time link to the NEW address.
 * Once the user clicks that link, ``/auth/email-confirm`` POSTs the token to
 * ``/auth/me/email/confirm``, the backend atomically swaps email ⇄
 * pending_email + rotates ``session_epoch``, and the caller is signed back
 * in with a fresh cookie.
 *
 * Error handling:
 *   - 409 ``email_in_use`` (target address already owned by another account
 *     in ``email`` or ``pending_email``) — render a targeted inline error.
 *   - 400/409 ``{detail: {code: "email_unchanged"}}`` — the caller asked to
 *     change to the address that's already on the account. We surface a
 *     dedicated inline error regardless of status code (the backend uses
 *     400 today; the FE doesn't need to care which) so the previous
 *     generic-toast path doesn't reappear if the contract shifts.
 *   - 422 — generic invalid-shape; show the message verbatim.
 *   - Any other status — fall back to the message in the body.
 *
 * The form respects the deletion-lock: when ``locked`` is true the submit
 * is disabled — the backend would 403 anyway, and the lock banner above
 * the tabs is the authoritative explanation.
 */

import * as React from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { User } from "@arena/shared-types";
import { ApiError, account } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Pull the structured ``detail.code`` out of an ``ApiErrorBody`` without
 * tripping over the three legal ``detail`` shapes (string, validation array,
 * object). Returns ``null`` when the body doesn't carry one — callers should
 * fall back to status-code branching.
 */
function readDetailCode(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return null;
  const code = (detail as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
}

export interface EmailChangeFormProps {
  user: User;
  locked: boolean;
  onDismiss(): void;
}

export function EmailChangeForm({ user, locked, onDismiss }: EmailChangeFormProps) {
  const queryClient = useQueryClient();
  const [email, setEmail] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (next: { new_email: string }) => account.requestEmailChange(next),
    onSuccess(_data, variables) {
      toast.success(`We sent a confirmation link to ${variables.new_email}.`);
      setEmail("");
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    },
    onError(err: unknown, variables) {
      let message = "Couldn't start the email change.";
      if (err instanceof ApiError) {
        // Branch on the structured ``detail.code`` first — it survives a
        // future status-code shuffle. Backend currently uses 400 for
        // ``email_unchanged``; the FE shouldn't care which 4xx envelope it
        // lands on.
        const detailCode = readDetailCode(err.body);
        if (detailCode === "email_unchanged") {
          message = "That's already your current email address.";
        } else if (err.status === 409) {
          message = `${variables.new_email} is already in use by another account.`;
        } else if (err.message) {
          message = err.message;
        }
      }
      setError(message);
      toast.error(message);
    },
  });

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (mutation.isPending || locked) return;
    const trimmed = email.trim();
    if (!trimmed) {
      setError("Enter the new email address.");
      return;
    }
    if (trimmed.length > 254) {
      setError("That email address is too long.");
      return;
    }
    if (!EMAIL_PATTERN.test(trimmed)) {
      setError("That doesn't look like a valid email address.");
      return;
    }
    if (trimmed.toLowerCase() === user.email.toLowerCase()) {
      // Mirrors the backend's ``email_unchanged`` 400 so the surface text is
      // identical regardless of whether the FE pre-flight or the API short-
      // circuits first.
      setError("That's already your current email address.");
      return;
    }
    setError(null);
    mutation.mutate({ new_email: trimmed });
  }

  // When a pending email is already set we render the resend surface —
  // the form is still usable to overwrite the pending value with a
  // different address.
  //
  // Note: no dedicated "Cancel pending change" CTA. Cancellation would
  // require a backend endpoint we don't yet expose (POSTing the current
  // email to /me/email/change trips the ``email_unchanged`` 400). Tracked
  // in P0_DESIGN as a future enhancement; the re-send link is the only
  // remaining affordance for now.
  const pendingMode = Boolean(user.pending_email);

  return (
    <form
      onSubmit={handleSubmit}
      noValidate
      className="space-y-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4"
      data-testid="email-change-form"
    >
      <div className="grid gap-1.5">
        <Label htmlFor="new-email">
          {pendingMode ? "Change to a different email" : "New email address"}
        </Label>
        <Input
          id="new-email"
          type="email"
          autoComplete="email"
          required
          maxLength={254}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@new-domain.com"
          disabled={mutation.isPending || locked}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "email-change-error" : undefined}
          className="font-mono"
          data-testid="new-email-input"
        />
        <p className="text-xs text-[var(--color-muted-foreground)]">
          We&rsquo;ll send a one-time confirmation link to the new address.
          Your current email keeps working until you click it.
        </p>
      </div>

      {error ? (
        <p
          id="email-change-error"
          role="alert"
          aria-live="polite"
          className="rounded-md border border-[oklch(from_var(--color-danger)_l_c_h/0.5)] bg-[oklch(from_var(--color-danger)_l_c_h/0.08)] px-3 py-2 text-xs text-[var(--color-danger)]"
        >
          {error}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="submit"
          disabled={mutation.isPending || locked}
          data-testid="submit-email-change"
        >
          {mutation.isPending ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : null}
          {mutation.isPending
            ? "Sending…"
            : pendingMode
              ? "Send to new address"
              : "Send confirmation link"}
        </Button>
        {!pendingMode ? (
          <Button
            type="button"
            variant="ghost"
            onClick={onDismiss}
            disabled={mutation.isPending}
          >
            Cancel
          </Button>
        ) : null}
      </div>
    </form>
  );
}
