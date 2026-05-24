"use client";

/**
 * P0-6 — "Sign out everywhere" affordance.
 *
 * Single button gated behind a small inline confirmation popover (no full
 * Dialog — this is reversible and far less destructive than account deletion,
 * so the GitHub-style email re-confirm would be over-friction).
 *
 * On confirm:
 *   1. POST ``/auth/me/sessions/sign-out-all`` — backend rotates the
 *      ``session_epoch``, invalidating every existing cookie, and re-mints
 *      a fresh one for the caller so they stay signed in here.
 *   2. Invalidate ``["me"]`` so any header / catalog surfaces re-fetch off
 *      the new identity. We deliberately do NOT call ``queryClient.clear()``
 *      — the user is the same person; only their other sessions are gone.
 */

import * as React from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, account } from "@/lib/api";
import { Button } from "@/components/ui/Button";

export interface SignOutAllButtonProps {
  disabled?: boolean;
}

export function SignOutAllButton({ disabled = false }: SignOutAllButtonProps) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = React.useState(false);

  const mutation = useMutation({
    mutationFn: () => account.signOutAll(),
    onSuccess() {
      toast.success("Signed out of every other session.");
      setConfirming(false);
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    },
    onError(err: unknown) {
      const message =
        err instanceof ApiError
          ? err.message || "Couldn't sign out other sessions."
          : "Couldn't sign out other sessions.";
      toast.error(message);
    },
  });

  if (!confirming) {
    return (
      <Button
        variant="secondary"
        onClick={() => setConfirming(true)}
        disabled={disabled || mutation.isPending}
        data-testid="sign-out-all-trigger"
      >
        Sign out everywhere
      </Button>
    );
  }

  return (
    <div
      role="group"
      aria-label="Confirm sign out everywhere"
      className="inline-flex items-center gap-2 rounded-md border border-[var(--color-border-strong)] bg-[var(--color-surface-elevated)] px-3 py-2"
      data-testid="sign-out-all-confirm-popover"
    >
      <p className="text-xs text-[var(--color-muted-foreground)]">
        Sign out on every device? You&rsquo;ll stay signed in on this one.
      </p>
      <Button
        size="sm"
        variant="destructive"
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
        data-testid="sign-out-all-confirm"
      >
        {mutation.isPending ? (
          <Loader2 className="size-4 animate-spin" aria-hidden />
        ) : null}
        Confirm
      </Button>
      <Button
        size="sm"
        variant="ghost"
        onClick={() => setConfirming(false)}
        disabled={mutation.isPending}
      >
        Cancel
      </Button>
    </div>
  );
}
