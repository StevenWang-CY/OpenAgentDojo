"use client";

/**
 * P0-6 — Danger tab.
 *
 * Two modes:
 *   1. No pending deletion → render the warning copy + the
 *      ``DeleteAccountDialog`` trigger.
 *   2. Deletion already scheduled → render a scoped countdown card with a
 *      "Cancel deletion" button. The account-wide ``DeletionLockBanner``
 *      already lives above the tabs; this card is the tab-local control
 *      surface so the user doesn't have to scroll up to recover.
 *
 * Visual treatment uses the destructive (red) token for the heading and the
 * primary CTA. Cancel-deletion uses ``warning`` styling (matches the lock
 * banner) so the user reads it as "back out" rather than "another
 * destructive action".
 */

import * as React from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { User } from "@arena/shared-types";
import { ApiError, account } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { SectionLabel } from "./AccountView";
import { DeleteAccountDialog } from "./DeleteAccountDialog";

function formatAbsolute(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export interface DangerPanelProps {
  user: User;
}

export function DangerPanel({ user }: DangerPanelProps) {
  const queryClient = useQueryClient();

  const cancelMutation = useMutation({
    mutationFn: () => account.cancelDeletion(),
    onSuccess() {
      toast.success("Deletion cancelled.");
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    },
    onError(err: unknown) {
      if (err instanceof ApiError && err.status === 410) {
        toast.error("The deletion grace has already elapsed.");
        return;
      }
      const message =
        err instanceof ApiError
          ? err.message || "Couldn't cancel deletion."
          : "Couldn't cancel deletion.";
      toast.error(message);
    },
  });

  const scheduled = user.deletion_scheduled_at;

  return (
    <section aria-labelledby="danger-heading" className="space-y-6">
      <header>
        <SectionLabel>danger</SectionLabel>
        <h2
          id="danger-heading"
          className="mt-1 text-lg font-semibold text-[var(--color-danger)]"
        >
          Delete your account
        </h2>
        <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
          Deletion runs on a 7-day grace timer. You can cancel any time
          during the grace from this page; once it elapses your data is
          permanently removed.
        </p>
      </header>

      {scheduled ? (
        <div
          className="space-y-3 rounded-lg border border-[oklch(from_var(--color-warning)_l_c_h/0.5)] bg-[oklch(from_var(--color-warning)_l_c_h/0.08)] p-4"
          data-testid="danger-scheduled-card"
        >
          <p className="text-sm font-semibold">
            Scheduled for {formatAbsolute(scheduled)}
          </p>
          <p className="text-xs text-[var(--color-muted-foreground)]">
            Cancel below to undo and restore mutating actions across the
            product. If you let the grace elapse the deletion is permanent —
            we can&rsquo;t restore the account afterwards.
          </p>
          <Button
            variant="secondary"
            onClick={() => cancelMutation.mutate()}
            disabled={cancelMutation.isPending}
            data-testid="cancel-deletion-danger"
          >
            {cancelMutation.isPending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : null}
            Cancel deletion
          </Button>
        </div>
      ) : (
        <div
          className="space-y-4 rounded-lg border border-[oklch(from_var(--color-danger)_l_c_h/0.5)] bg-[oklch(from_var(--color-danger)_l_c_h/0.04)] p-4"
          data-testid="danger-idle-card"
        >
          <ul className="ml-5 list-disc space-y-1 text-xs text-[var(--color-muted-foreground)]">
            <li>All your sessions, submissions, badges, and prompts are removed.</li>
            <li>
              Your handle and email are tombstoned (cannot be re-registered).
              Your public profile URL returns a clean 404.
            </li>
            <li>
              References from other users&rsquo; histories (the multi-attempt
              audit trail) are nulled out, not surfaced as deleted-user
              attribution.
            </li>
            <li>
              The grace gives you 7 days to undo. After that the daily worker
              hard-deletes the row and the operation cannot be reversed.
            </li>
          </ul>
          <DeleteAccountDialog user={user} />
        </div>
      )}
    </section>
  );
}
