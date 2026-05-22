"use client";

import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, Send } from "lucide-react";
import { toast } from "sonner";
import type { SupervisionEvent } from "@arena/shared-types";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { ApiError, submitSession } from "@/lib/api";
import { VerificationChecklist } from "./VerificationChecklist";
import { track } from "@/lib/telemetry";

interface SubmitDialogProps {
  sessionId: string;
  events: SupervisionEvent[];
  /** Override the trigger if you want to use a different button. */
  trigger?: React.ReactNode;
  /**
   * Forwarded ref to the default trigger button. The workspace top bar uses
   * this to bind a global ⌘⏎ / Ctrl+⏎ shortcut without coupling state.
   */
  triggerRef?: React.Ref<HTMLButtonElement>;
  /** Render the platform-aware keyboard hint next to the Submit label. */
  showShortcutHint?: boolean;
  /** Whether the platform is macOS — drives ⌘ vs. Ctrl in the kbd hint. */
  isMac?: boolean;
  /**
   * Retained for backward compatibility — callers can still pass a
   * post-submission callback. This dialog no longer fires it, because
   * the `submitSession` response is a not-yet-graded `Submission` (the
   * backend kicks off async grading). `WorkspaceShell` drives the
   * report navigation off `status === "graded"` instead.
   */
  onSubmitted?(submissionId: string): void;
}

export function SubmitDialog({
  sessionId,
  events,
  trigger,
  triggerRef,
  showShortcutHint,
  isMac,
}: SubmitDialogProps) {
  const [open, setOpen] = React.useState(false);

  const mutation = useMutation({
    mutationFn: () => {
      track("submission_started", { session_id: sessionId });
      return submitSession(sessionId);
    },
    onSuccess() {
      // Submission only kicks off grading — the report doesn't exist yet.
      // Closing the dialog and surfacing a "now grading" toast is enough:
      // `WorkspaceShell` is already polling the session and will navigate
      // to /report/{id} once the status flips to `graded`.
      //
      // Hold the toast longer than sonner's 3.5s default so it stays
      // visible across the WorkspaceShell's status-driven redirect to
      // the report page — otherwise the user often sees nothing and
      // wonders whether the click registered.
      setOpen(false);
      toast.success("Submitted. Grading…", { duration: 7_000 });
    },
    onError(error) {
      const msg =
        error instanceof ApiError
          ? error.status === 0
            ? "Couldn't reach the API. Is the backend running?"
            : error.message
          : "Failed to submit.";
      toast.error(msg);
    },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {trigger ?? (
          <Button
            ref={triggerRef}
            type="button"
            variant="primary"
            aria-keyshortcuts={
              showShortcutHint ? (isMac ? "Meta+Enter" : "Control+Enter") : undefined
            }
          >
            <Send className="size-3.5" aria-hidden /> Submit
            {showShortcutHint ? (
              <kbd
                aria-hidden
                className="ml-1 inline-flex items-center gap-0.5 rounded border border-[oklch(from_var(--color-primary-foreground)_l_c_h/0.35)] bg-[oklch(from_var(--color-primary-foreground)_l_c_h/0.15)] px-1 font-mono text-[10px] leading-none"
              >
                {isMac ? "⌘" : "Ctrl"}
                <span aria-hidden>⏎</span>
              </kbd>
            ) : null}
          </Button>
        )}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Submit your supervision attempt</DialogTitle>
          <DialogDescription>
            Once you submit, hidden tests and structural validators run against
            your final diff. The sandbox is frozen and can&rsquo;t be modified
            after that.
          </DialogDescription>
        </DialogHeader>

        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-muted)] p-4">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]">
            Quick checklist
          </p>
          <VerificationChecklist events={events} className="mt-3" />
        </div>

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => setOpen(false)}
            disabled={mutation.isPending}
          >
            Cancel
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
            {mutation.isPending ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <Send className="size-3.5" aria-hidden />
            )}
            {mutation.isPending ? "Submitting…" : "Submit and grade"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
