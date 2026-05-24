"use client";

/**
 * P0-6 — Deletion-lock banner.
 *
 * The FE counterpart to the backend's "deletion-scheduled lockout"
 * middleware: while ``users.deletion_scheduled_at`` is set, every mutating
 * endpoint except ``/me/delete/cancel`` returns 403 with
 * ``{code: "deletion_scheduled", scheduled_for}``. Without this banner the
 * user would see a series of opaque 403s and have no idea why.
 *
 * Tone is ``warning`` (amber) — not destructive (red). Deletion has been
 * scheduled but is recoverable for the full 7-day grace; the visual cue
 * should be "heads up, this is on a timer" rather than "alarm, alarm".
 * ADR rationale mirrors the give-up dialog (also amber).
 *
 * The banner re-computes the remaining countdown once a minute on mount.
 * A second-level tick would be over-precise (the grace is days, not seconds)
 * and would burn React reconciles unnecessarily.
 */

import * as React from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, account } from "@/lib/api";
import { Button } from "@/components/ui/Button";

const TICK_MS = 60_000;

function describeRemaining(scheduledFor: string): string {
  const target = new Date(scheduledFor).getTime();
  if (!Number.isFinite(target)) return "soon";
  const remainingMs = target - Date.now();
  if (remainingMs <= 0) return "any moment now";
  const days = Math.floor(remainingMs / (24 * 60 * 60 * 1000));
  const hours = Math.floor((remainingMs % (24 * 60 * 60 * 1000)) / (60 * 60 * 1000));
  if (days >= 1) {
    const noun = days === 1 ? "day" : "days";
    const hourNoun = hours === 1 ? "hour" : "hours";
    return `${days} ${noun}, ${hours} ${hourNoun} remaining`;
  }
  const minutes = Math.floor((remainingMs % (60 * 60 * 1000)) / (60 * 1000));
  if (hours >= 1) {
    return `${hours}h ${minutes}m remaining`;
  }
  return `${minutes}m remaining`;
}

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

export interface DeletionLockBannerProps {
  scheduledFor: string;
}

export function DeletionLockBanner({ scheduledFor }: DeletionLockBannerProps) {
  const queryClient = useQueryClient();
  const [now, setNow] = React.useState(() => Date.now());

  React.useEffect(() => {
    const handle = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(handle);
  }, []);

  // ``now`` drives the minute-resolution re-render; ``describeRemaining``
  // reads ``Date.now()`` itself so the rendered countdown lines up with
  // the wall clock at paint time (and not the slightly-earlier ``setNow``
  // tick — important when fake timers are in play). The ``useMemo`` ties
  // the two together so the dependency on ``now`` is load-bearing rather
  // than a ``void now;`` sentinel masquerading as one.
  const remaining = React.useMemo(
    () => describeRemaining(scheduledFor),
    // ``now`` is intentionally a dependency: it bumps every TICK_MS so the
    // memoised string re-computes off the current ``Date.now()``.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [scheduledFor, now],
  );

  const cancelMutation = useMutation({
    mutationFn: () => account.cancelDeletion(),
    onSuccess() {
      toast.success("Deletion cancelled. Your account is back to normal.");
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    },
    onError(err: unknown) {
      if (err instanceof ApiError && err.status === 410) {
        toast.error("The deletion grace has already elapsed.");
        return;
      }
      const message =
        err instanceof ApiError
          ? err.message || "Couldn't cancel the deletion."
          : "Couldn't cancel the deletion.";
      toast.error(message);
    },
  });

  return (
    <aside
      role="alert"
      aria-live="polite"
      data-tone="warning"
      data-testid="deletion-lock-banner"
      className="mb-6 flex flex-col gap-3 rounded-lg border border-[oklch(from_var(--color-warning)_l_c_h/0.5)] bg-[oklch(from_var(--color-warning)_l_c_h/0.08)] p-4 sm:flex-row sm:items-start sm:justify-between"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle
          className="mt-0.5 size-4 shrink-0 text-[var(--color-warning)]"
          aria-hidden
        />
        <div>
          <p className="text-sm font-semibold">Account scheduled for deletion</p>
          <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">
            Your account will be permanently removed on{" "}
            <span className="font-medium text-[var(--color-foreground)]">
              {formatAbsolute(scheduledFor)}
            </span>{" "}
            ·{" "}
            <span data-testid="deletion-countdown">{remaining}</span>
            . Until then your account is read-only — other actions will
            return an error. Cancel below to undo.
          </p>
        </div>
      </div>
      <div className="shrink-0">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => cancelMutation.mutate()}
          disabled={cancelMutation.isPending}
          data-testid="cancel-deletion-banner"
        >
          {cancelMutation.isPending ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : null}
          Cancel deletion
        </Button>
      </div>
    </aside>
  );
}
