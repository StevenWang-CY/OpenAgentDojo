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
  //
  // FE-P1 audit fix — also resync ``now`` whenever the page becomes
  // visible again. Background tabs are throttled to ~1Hz (Chrome) or
  // paused entirely (Safari), so a user who backgrounded the tab for
  // 10+ minutes would otherwise see a stale countdown until the next
  // interval tick. The visibilitychange handler force-resyncs the
  // moment they look at the tab again so the button enables exactly
  // when the server's gate opens.
  React.useEffect(() => {
    const startedMs = new Date(sessionStartedAt).getTime();
    if (Number.isNaN(startedMs)) return;
    const elapsed = (Date.now() - startedMs) / 1000;
    if (elapsed >= GIVE_UP_MIN_SECONDS) return;
    const interval = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    const onVisibility = () => {
      if (!document.hidden) setNow(Date.now());
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onVisibility);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onVisibility);
    };
  }, [sessionStartedAt]);

  // FE-P2 audit fix — a malformed ``sessionStartedAt`` (e.g. backend
  // shipped a stale-cache value or the cookie deserialiser failed) used
  // to fall back to ``Date.now()``, which opened the gate IMMEDIATELY
  // because elapsed = 0. Fall back to "the future" instead so the
  // button stays disabled and the user / ops sees the unhealthy state
  // rather than a silently-bypassed gate.
  const startedMs = React.useMemo(() => {
    const ms = new Date(sessionStartedAt).getTime();
    if (!Number.isFinite(ms)) {
      // Pretend the session started 10 years from now → secondsRemaining
      // saturates and the gate stays closed. Surfaces as a log warning.
      // (We deliberately don't throw — workspace shell tolerates a stale
      // started_at by displaying the rest of the surface read-only.)
      if (process.env.NODE_ENV !== "production") {
        console.warn(
          "[GiveUpDialog] invalid sessionStartedAt:",
          sessionStartedAt,
        );
      }
      return Date.now() + 10 * 365 * 24 * 60 * 60 * 1000;
    }
    return ms;
  }, [sessionStartedAt]);
  const elapsedSeconds = Math.max(0, Math.floor((now - startedMs) / 1000));
  const secondsRemaining = Math.max(0, GIVE_UP_MIN_SECONDS - elapsedSeconds);
  const isGateOpen = secondsRemaining === 0;

  async function handleConfirm() {
    if (busy) return;
    setBusy(true);
    try {
      const submission = await giveUpSession(sessionId);
      // P0-4 audit fix — telemetry fires AFTER the API accepts. Firing
      // before the call conflated user intent with server rejections
      // (425 gate-not-elapsed, 409 not-active) and inflated the abandon
      // signal.
      track("session_gave_up", { session_id: sessionId });
      // Don't await the toast — the redirect closes the modal anyway and
      // re-rendering the workspace shell would race with the navigation.
      toast.success("Ideal solution revealed on the report page.");
      // P0-4 audit fix — close the dialog ONLY on success. Closing in
      // the finally block would race the error toast (the dialog would
      // close before the user could read what went wrong).
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
          // 409 split: tutorial sessions get a distinct, non-error toast
          // ("just complete or skip"); other 409s ("session not active")
          // get the existing error toast.
          const code = readErrorCode(err.body);
          if (code === "give_up_not_supported_for_tutorial") {
            toast.message(
              "Give up isn't available on the orientation tutorial — just complete or skip it.",
            );
          } else {
            toast.error(err.message || "Session is no longer active.");
          }
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
          {/* P0-4 audit fix — Warning treatment (amber), NOT destructive
              (red). ADR 0010: give-up is deliberate but recoverable.
              Implemented inline so we don't need to fork the Button
              variant table; the className override applies the warning
              tokens consistently with the trigger above. */}
          <Button
            type="button"
            variant="primary"
            onClick={() => void handleConfirm()}
            disabled={busy}
            data-testid="give-up-confirm"
            className={cn(
              "bg-[var(--color-warning)] text-[var(--color-warning-foreground,#1a1208)]",
              "hover:brightness-110 active:brightness-95 shadow-soft",
            )}
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

/**
 * Read ``detail.code`` off a structured ApiError body. The backend uses
 * stable string codes (e.g. ``give_up_not_supported_for_tutorial``) so the
 * FE can branch on the semantic meaning instead of HTTP status + message
 * substring matching.
 */
function readErrorCode(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return null;
  const code = (detail as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
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
