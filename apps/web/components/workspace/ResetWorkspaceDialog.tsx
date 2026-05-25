"use client";

/**
 * P0-12 — Reset workspace affordance.
 *
 * Confirm dialog that wraps ``POST /api/v1/sessions/{id}/reset``. The
 * server runs ``git reset --hard <initial_commit>`` + ``git clean -fd``
 * inside the sandbox, then emits a typed ``session.reset`` event so the
 * supervision timeline + post-mortem walkthrough can both see the
 * backtrack.
 *
 * Visual treatment is **neutral** by design — the design doc calls out
 * that the *button* (in the topbar overflow menu) must be neutral so the
 * affordance feels free, not shameful. The dialog explains the cost in
 * full but does NOT use destructive (red) treatment; warning (amber) is
 * the right band for "deliberate but recoverable."
 */

import * as React from "react";
import { Loader2, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  resetSession,
  type SessionResetResponse,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { track } from "@/lib/telemetry";
import { useWorkspaceStore } from "@/stores/workspaceStore";
import { cn } from "@/lib/utils";

export interface ResetWorkspaceDialogProps {
  sessionId: string;
  /** Controlled-open hook so the overflow menu can drive the dialog. */
  open: boolean;
  onOpenChange(open: boolean): void;
  /** Optional success hook for parent surfaces that want to react
   *  (clear local UI state, scroll the timeline, etc.). The store's own
   *  WS-driven invalidation already covers the workspace; this is just
   *  for opt-in side effects. */
  onReset?(response: SessionResetResponse): void;
}

export function ResetWorkspaceDialog({
  sessionId,
  open,
  onOpenChange,
  onReset,
}: ResetWorkspaceDialogProps) {
  const [busy, setBusy] = React.useState(false);
  const queryClient = useQueryClient();
  const store = useWorkspaceStore(sessionId);
  const resetWorkspaceForReset = store((s) => s.resetForWorkspaceReset);
  // Read the reset count from prior session.reset events already in the
  // store — the dialog is honest about how many times the user has used
  // the affordance inside this session. The number lives ONLY inside the
  // dialog (per design); the trigger button stays neutral.
  const priorResetCount = store(
    (s) => s.events.filter((e) => e.event_type === "session.reset").length,
  );

  // Defensive against React 18 strict-mode + concurrent unmounts: never
  // call setState after the component has gone away.
  const mountedRef = React.useRef(true);
  React.useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  async function handleConfirm() {
    if (busy) return;
    // Telemetry MUST fire on confirm (not on overflow-menu click), with
    // a count of file buffers that will be discarded. Read once via
    // ``getState()`` so we don't add a subscription just for this number.
    const filesEstimate = Object.keys(store.getState().fileBuffers).length;
    track("session_reset_requested", {
      session_id: sessionId,
      files_discarded_estimate: filesEstimate,
    });
    setBusy(true);
    try {
      const response = await resetSession(sessionId);
      // Drop every per-file buffer + invalidate the workspace queries so
      // the file tree, diff viewer, and individual file editors all
      // re-fetch against the post-reset state. Telemetry fires AFTER
      // the API accepts so a rejected reset doesn't inflate the signal.
      resetWorkspaceForReset();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["session", sessionId, "tree"] }),
        queryClient.invalidateQueries({ queryKey: ["session", sessionId, "diff"] }),
        queryClient.invalidateQueries({ queryKey: ["file", sessionId] }),
      ]);
      track("session_reset_completed", {
        session_id: sessionId,
        reset_count: response.reset_count,
        files_reset: response.files_reset,
      });
      toast.success("Workspace reset to initial commit.");
      onReset?.(response);
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          toast.error(err.message || "Session is no longer active.");
        } else if (err.status === 500) {
          toast.error(
            "Reset failed inside the sandbox — please refresh and try again.",
          );
        } else {
          toast.error(err.message || "Could not reset — try again.");
        }
      } else {
        toast.error("Could not reset — try again.");
      }
    } finally {
      if (mountedRef.current) {
        setBusy(false);
      }
    }
  }

  // While the POST is in flight, ESC / overlay-click / X must be no-ops:
  // we already disable Cancel/Confirm, but the Dialog primitive itself
  // still fires ``onOpenChange(false)`` on those gestures. Swallow that
  // call so the in-flight request isn't dropped and the post-success
  // cache-invalidation isn't skipped.
  const handleOpenChange = React.useCallback(
    (next: boolean) => {
      if (busy && !next) return;
      onOpenChange(next);
    },
    [busy, onOpenChange],
  );

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-md" data-testid="reset-workspace-dialog">
        <DialogHeader>
          <DialogTitle>Reset workspace?</DialogTitle>
          <DialogDescription>
            Roll the files back to the mission&apos;s initial commit. Your
            file edits and the agent&apos;s patches will be discarded.
          </DialogDescription>
        </DialogHeader>

        <ul
          className="space-y-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-3 text-xs text-[var(--color-muted-foreground)]"
          aria-label="What happens when you reset"
        >
          <li className="flex items-start gap-2">
            <span aria-hidden className="font-mono text-[var(--color-warning)]">
              ▸
            </span>
            <span>
              Your supervision timeline stays — the grader will see this as
              a <span className="font-mono">session.reset</span> event.
            </span>
          </li>
          <li className="flex items-start gap-2">
            <span aria-hidden className="font-mono text-[var(--color-warning)]">
              ▸
            </span>
            <span>
              Your prompts and the agent&apos;s narration stay too — only
              the files in the workspace go back to initial state.
            </span>
          </li>
          <li className="flex items-start gap-2">
            <span aria-hidden className="font-mono text-[var(--color-warning)]">
              ▸
            </span>
            <span>
              You&apos;ve reset this session{" "}
              <span
                className="font-mono font-semibold text-[var(--color-foreground)]"
                data-testid="reset-count"
              >
                {priorResetCount}
              </span>{" "}
              time{priorResetCount === 1 ? "" : "s"} so far.
            </span>
          </li>
        </ul>

        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="primary"
            onClick={() => void handleConfirm()}
            disabled={busy}
            data-testid="reset-workspace-confirm"
            className={cn(
              "bg-[var(--color-warning)] text-[var(--color-warning-foreground,#1a1208)]",
              "hover:brightness-110 active:brightness-95 shadow-soft",
            )}
          >
            {busy ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <Undo2 className="size-3.5" aria-hidden />
            )}
            {busy ? "Resetting…" : "Yes, reset"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
