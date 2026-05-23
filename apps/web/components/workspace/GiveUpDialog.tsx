"use client";

/**
 * P0-4 — Give-up affordance.
 *
 * Wraps the workspace's secondary "Give up & reveal" button in a Radix Dialog
 * that:
 *
 *   1. Soft-blocks the button for the first 10 minutes of the session
 *      (server-enforced; the FE just hides the click hazard with a tooltip).
 *   2. Explains the cost: a 50/100 hard cap on the resulting submission,
 *      a ``gave_up`` chip on the report header, and a "Retry mission" link
 *      on the report page for a clean second attempt.
 *   3. On confirm, calls ``api.giveUpSession(sessionId)`` which triggers
 *      the standard submit pipeline — the WorkspaceShell already renders
 *      the ``GradingWait`` view for ``submitting`` and routes to /report.
 *
 * The visual treatment uses the warning token (amber/yellow), NOT the
 * danger (red) treatment — "give up" is deliberate but recoverable, not
 * destructive. ADR 0010 walks through this choice.
 */

import * as React from "react";
import { useRouter } from "next/navigation";
import { Flag, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { ApiError, giveUpSession } from "@/lib/api";
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
import { track } from "@/lib/telemetry";
import { cn } from "@/lib/utils";

const GIVE_UP_MIN_SECONDS = 600; // mirror sessions/router.GIVE_UP_MIN_SECONDS

export interface GiveUpDialogProps {
  sessionId: string;
  /** Wall-clock moment the session started, used to drive the local
   *  countdown so the button enables exactly when the server gate opens. */
  sessionStartedAt: string;
  /** Optional class injected on the trigger button — lets the topbar
   *  vary spacing without re-styling the variant. */
  className?: string;
  /** Optional success hook — the dialog never navigates by itself; the
   *  parent (WorkspaceShell or topbar) decides what to do once the
   *  Submission resolves. The submission carries score_cap_reason
   *  so the report page can render the gave-up chip immediately. */
  onSubmitted?(submissionId: string): void;
}

export function GiveUpDialog({
  sessionId,
  sessionStartedAt,
  className,
  onSubmitted,
}: GiveUpDialogProps) {
  const [open, setOpen] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [now, setNow] = React.useState(() => Date.now());
  const router = useRouter();

  // Tick once a second so the countdown decreases in real time. We only
  // run the tick while the gate hasn't elapsed; afterwards the interval
  // shuts itself down so we don't churn React state for nothing.
  React.useEffect(() => {
    const startedMs = new Date(sessionStartedAt).getTime();
    if (Number.isNaN(startedMs)) return;
    const elapsed = (Date.now() - startedMs) / 1000;
    if (elapsed >= GIVE_UP_MIN_SECONDS) return;
    const interval = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => window.clearInterval(interval);
  }, [sessionStartedAt]);

  const startedMs = React.useMemo(() => {
    const ms = new Date(sessionStartedAt).getTime();
    return Number.isFinite(ms) ? ms : Date.now();
  }, [sessionStartedAt]);
  const elapsedSeconds = Math.max(0, Math.floor((now - startedMs) / 1000));
  const secondsRemaining = Math.max(0, GIVE_UP_MIN_SECONDS - elapsedSeconds);
  const isGateOpen = secondsRemaining === 0;

  async function handleConfirm() {
    if (busy) return;
    setBusy(true);
    track("session_gave_up", { session_id: sessionId });
    try {
      const submission = await giveUpSession(sessionId);
      // Don't await the toast — the redirect closes the modal anyway and
      // re-rendering the workspace shell would race with the navigation.
      toast.success("Ideal solution revealed on the report page.");
      setOpen(false);
      onSubmitted?.(submission.id);
      router.push(`/report/${submission.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 425) {
          const remaining = readSecondsRemaining(err.body);
          toast.error(
            remaining !== null
              ? `Give up unlocks in ${formatCountdown(remaining)}.`
              : "Give up isn't available yet — keep going.",
          );
        } else if (err.status === 409) {
          toast.error(err.message || "Session is no longer active.");
        } else {
          toast.error(err.message || "Could not give up — try again.");
        }
      } else {
        toast.error("Could not give up — try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <button
          type="button"
          disabled={!isGateOpen}
          aria-disabled={!isGateOpen}
          title={
            isGateOpen
              ? "Give up and reveal the ideal solution (caps score at 50)"
              : `Available in ${formatCountdown(secondsRemaining)}`
          }
          data-testid="give-up-trigger"
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs",
            "transition-[background-color,box-shadow,color] duration-150 ease-macos",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-background)]",
            isGateOpen
              ? "border-[oklch(from_var(--color-warning)_l_c_h/0.45)] bg-[oklch(from_var(--color-warning)_l_c_h/0.12)] text-[var(--color-foreground)] hover:bg-[oklch(from_var(--color-warning)_l_c_h/0.2)]"
              : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-muted-foreground)] opacity-70 cursor-not-allowed",
            className,
          )}
        >
          <Flag className="size-3.5" aria-hidden />
          <span>
            {isGateOpen
              ? "Give up & reveal"
              : `Give up · ${formatCountdownShort(secondsRemaining)}`}
          </span>
        </button>
      </DialogTrigger>

      <DialogContent
        className="max-w-md"
        data-testid="give-up-dialog"
      >
        <DialogHeader>
          <DialogTitle>Give up and reveal the ideal solution?</DialogTitle>
          <DialogDescription>
            This caps your score for this attempt at{" "}
            <span className="font-mono font-semibold text-[var(--color-foreground)]">
              50 / 100
            </span>
            . The submission stays on your profile with a{" "}
            <span className="font-mono">gave up</span> chip — no hiding.
          </DialogDescription>
        </DialogHeader>

        <ul
          className="space-y-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-3 text-xs text-[var(--color-muted-foreground)]"
          aria-label="What happens when you give up"
        >
          <li className="flex items-start gap-2">
            <span aria-hidden className="font-mono text-[var(--color-warning)]">
              ▸
            </span>
            <span>
              Your supervision is graded with the work you&apos;ve done so
              far — no further changes are applied.
            </span>
          </li>
          <li className="flex items-start gap-2">
            <span aria-hidden className="font-mono text-[var(--color-warning)]">
              ▸
            </span>
            <span>
              The ideal solution + post-mortem walkthrough appears on the
              report page. Read it carefully before retrying.
            </span>
          </li>
          <li className="flex items-start gap-2">
            <span aria-hidden className="font-mono text-[var(--color-warning)]">
              ▸
            </span>
            <span>
              You can <strong>retry this mission</strong> later for a
              clean attempt — the best uncapped attempt wins on your
              profile.
            </span>
          </li>
        </ul>

        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={() => setOpen(false)}
            disabled={busy}
          >
            Stay in the mission
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={() => void handleConfirm()}
            disabled={busy}
            data-testid="give-up-confirm"
          >
            {busy ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <Flag className="size-3.5" aria-hidden />
            )}
            {busy ? "Submitting…" : "Yes, give up"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function readSecondsRemaining(body: unknown): number | null {
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return null;
  const value = (detail as { seconds_remaining?: unknown }).seconds_remaining;
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) {
    return Math.ceil(value);
  }
  return null;
}

function formatCountdown(totalSeconds: number): string {
  if (totalSeconds <= 0) return "0s";
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes === 0) return `${seconds}s`;
  if (seconds === 0) return `${minutes}m`;
  return `${minutes}m ${seconds.toString().padStart(2, "0")}s`;
}

function formatCountdownShort(totalSeconds: number): string {
  if (totalSeconds <= 0) return "ready";
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes === 0) return `${seconds}s`;
  return `${minutes}m${seconds.toString().padStart(2, "0")}`;
}
