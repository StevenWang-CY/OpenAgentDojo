"use client";

/**
 * P0-6 — Account deletion dialog.
 *
 * GitHub-style high-friction confirmation: the user must re-type their email
 * verbatim before the destructive button enables. This is also what makes
 * the dialog test-friendly — the destructive action can't fire on a stray
 * keypress or auto-fill.
 *
 * On success the dialog closes; the parent re-renders with
 * ``deletion_scheduled_at`` populated, which surfaces the
 * ``DeletionLockBanner`` at the top of the page.
 *
 * The 403 ``deletion_scheduled`` path can only happen on a stale render
 * (the user already kicked deletion in another tab — the ``DeletionLockMiddleware``
 * blocks ``POST /me/delete`` while ``deletion_scheduled_at`` is set and
 * returns 403 with body ``{code: "deletion_scheduled", scheduled_for}``).
 * We surface it as a soft toast rather than an error since the desired end
 * state has already been reached, then invalidate ``["me"]`` so the lock
 * banner appears on the next render.
 */

import * as React from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { User } from "@arena/shared-types";
import { ApiError, account } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";

export interface DeleteAccountDialogProps {
  user: User;
}

export function DeleteAccountDialog({ user }: DeleteAccountDialogProps) {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [confirmEmail, setConfirmEmail] = React.useState("");

  const matches = confirmEmail.trim().toLowerCase() === user.email.toLowerCase();

  const mutation = useMutation({
    mutationFn: () => account.scheduleDeletion({ confirm_email: confirmEmail.trim() }),
    onSuccess(data) {
      toast.success(
        `Deletion scheduled for ${new Date(data.scheduled_for).toLocaleString()}.`,
      );
      setOpen(false);
      setConfirmEmail("");
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    },
    onError(err: unknown) {
      if (
        err instanceof ApiError &&
        err.status === 403 &&
        err.body?.code === "deletion_scheduled"
      ) {
        // Already scheduled in another tab — treat as success for the user.
        // The DeletionLockMiddleware returns 403 (not 409) with the
        // ``deletion_scheduled`` code while ``deletion_scheduled_at`` is set,
        // so branch on the code rather than status alone.
        toast.message("Deletion is already scheduled for this account.");
        setOpen(false);
        setConfirmEmail("");
        void queryClient.invalidateQueries({ queryKey: ["me"] });
        return;
      }
      const message =
        err instanceof ApiError
          ? err.message || "Couldn't schedule deletion."
          : "Couldn't schedule deletion.";
      toast.error(message);
    },
  });

  function handleOpenChange(next: boolean) {
    if (mutation.isPending) return; // don't drop the request mid-flight
    setOpen(next);
    if (!next) {
      setConfirmEmail("");
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant="destructive" data-testid="open-delete-dialog">
          Start 7-day deletion
        </Button>
      </DialogTrigger>
      <DialogContent data-testid="delete-account-dialog">
        <DialogHeader>
          <DialogTitle className="text-[var(--color-danger)]">
            Delete your account?
          </DialogTitle>
          <DialogDescription>
            This starts a 7-day grace period. You can cancel at any time from
            this page during the grace. After it elapses, your sessions,
            submissions, badges, and profile are permanently removed; your
            email is tombstoned so it cannot be re-registered.
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!matches || mutation.isPending) return;
            mutation.mutate();
          }}
          noValidate
          className="space-y-3"
        >
          <div className="grid gap-1.5">
            <Label htmlFor="confirm-email">
              Type{" "}
              <span className="font-mono text-[var(--color-foreground)]">
                {user.email}
              </span>{" "}
              to confirm
            </Label>
            <Input
              id="confirm-email"
              type="email"
              autoComplete="off"
              value={confirmEmail}
              onChange={(e) => setConfirmEmail(e.target.value)}
              disabled={mutation.isPending}
              aria-invalid={confirmEmail.length > 0 && !matches ? true : undefined}
              className="font-mono"
              data-testid="confirm-email-input"
            />
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => handleOpenChange(false)}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="destructive"
              disabled={!matches || mutation.isPending}
              data-testid="confirm-delete"
            >
              {mutation.isPending ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : null}
              Start 7-day deletion
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
