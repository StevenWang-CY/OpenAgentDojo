"use client";

import * as React from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ChevronDown,
  ChevronLeft,
  Gauge,
  MoreHorizontal,
  Undo2,
} from "lucide-react";
import type { Difficulty, SupervisionEvent } from "@arena/shared-types";
import { Badge } from "@/components/ui/Badge";
import { DifficultyBadge } from "@/components/catalog/DifficultyBadge";
import { GiveUpDialog } from "./GiveUpDialog";
import { ResetWorkspaceDialog } from "./ResetWorkspaceDialog";
import { ScorePreview } from "./ScorePreview";
import { SubmitDialog } from "./SubmitDialog";
import { cn } from "@/lib/utils";

/**
 * Detect macOS so we can render `⌘⏎` vs. `Ctrl+⏎` for keyboard hints.
 * SSR-safe: defaults to `false` until hydration runs the effect.
 */
function useIsMac(): boolean {
  const [isMac, setIsMac] = React.useState(false);
  React.useEffect(() => {
    const ua = typeof navigator !== "undefined" ? navigator.userAgent : "";
    setIsMac(/Mac|iPhone|iPad|iPod/i.test(ua));
  }, []);
  return isMac;
}

interface WorkspaceTopBarProps {
  sessionId: string;
  missionTitle: string;
  missionId: string;
  difficulty: Difficulty;
  sandboxDriver: "docker" | "local" | "unknown";
  events: SupervisionEvent[];
  /** Required-context paths (from mission.expected_context_required). */
  expectedRequiredContext: string[];
  /** Files the user has currently marked as context. */
  selectedContext: string[];
  /** Optional list of changed paths in the current diff. */
  diffChangedFiles?: string[];
  /** P0-4 — ISO timestamp of session.started_at; drives the 10-min countdown
   *  on the GiveUpDialog so the button enables exactly when the server gate
   *  opens. Omitted on tutorial sessions (the give-up affordance is hidden
   *  there). */
  sessionStartedAt?: string;
  /** P0-4 — when true, render the give-up affordance beside Submit. Set
   *  to false for tutorial missions (which short-circuit grading). */
  showGiveUp?: boolean;
  onSubmitted?(submissionId: string): void;
}

/**
 * Workspace top bar. Always shows a compact "Process signals" pill — a single
 * "X/Y signals" tally that expands an inline panel with the full
 * `ScorePreview` so the user never has to dig for the process score.
 */
export function WorkspaceTopBar({
  sessionId,
  missionTitle,
  missionId,
  difficulty,
  sandboxDriver,
  events,
  expectedRequiredContext,
  selectedContext,
  diffChangedFiles,
  sessionStartedAt,
  showGiveUp = true,
  onSubmitted,
}: WorkspaceTopBarProps) {
  const [open, setOpen] = React.useState(false);
  const isMac = useIsMac();
  const submitTriggerRef = React.useRef<HTMLButtonElement | null>(null);
  const summary = React.useMemo(
    () => summarisePillSignals(expectedRequiredContext, selectedContext, events),
    [expectedRequiredContext, selectedContext, events]
  );

  // Global ⌘⏎ / Ctrl+⏎ to open the submit dialog. Ignored when focus is on a
  // text input/textarea so AgentChat keeps its own send shortcut intact.
  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Enter") return;
      if (!(e.metaKey || e.ctrlKey)) return;
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "TEXTAREA" ||
          target.tagName === "INPUT" ||
          target.isContentEditable)
      ) {
        return;
      }
      e.preventDefault();
      submitTriggerRef.current?.click();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="sticky top-0 z-30 flex flex-col gap-2 border-b border-[var(--color-border)] bg-[oklch(from_var(--color-surface)_l_c_h/0.85)] px-4 py-2 backdrop-blur supports-[backdrop-filter]:bg-[oklch(from_var(--color-surface)_l_c_h/0.7)]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <Link
            href="/missions"
            className="inline-flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] transition-colors duration-150 ease-macos hover:text-[var(--color-foreground)]"
          >
            <ChevronLeft className="size-3.5" aria-hidden />
            Missions
          </Link>
          <span aria-hidden className="text-[var(--color-muted-foreground)]">
            ·
          </span>
          <h1 className="truncate text-sm font-semibold tracking-tight">
            {missionTitle}
          </h1>
          <DifficultyBadge difficulty={difficulty} />
          <Badge tone="outline" className="font-mono text-[10px] tracking-normal">
            {missionId}
          </Badge>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setOpen((prev) => !prev)}
            aria-expanded={open}
            aria-controls="process-signals-panel"
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 text-xs",
              "transition-[background-color,box-shadow] duration-150 ease-macos hover:bg-[var(--color-muted)]",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-background)]",
              open && "bg-[var(--color-muted)] shadow-soft"
            )}
            data-testid="score-pill"
          >
            <Gauge className="size-3.5 text-[var(--color-primary)]" aria-hidden />
            <span className="font-mono">
              {summary.hit}/{summary.total}
            </span>
            <span className="text-[var(--color-muted-foreground)]">signals</span>
            <ChevronDown
              className={cn(
                "size-3 text-[var(--color-muted-foreground)] transition-transform duration-180 ease-macos",
                open && "rotate-180"
              )}
              aria-hidden
            />
          </button>
          {showGiveUp && sessionStartedAt ? (
            <GiveUpDialog
              sessionId={sessionId}
              sessionStartedAt={sessionStartedAt}
              onSubmitted={onSubmitted}
            />
          ) : null}
          {/* P0-12 — overflow menu carries the "Reset workspace" affordance.
              Rendered as a neutral icon button so the trigger feels free,
              not shameful (per design); the dialog itself surfaces the
              count + cost. */}
          <WorkspaceOverflowMenu sessionId={sessionId} />
          <div data-tutorial-anchor="submit-button" className="contents">
            <SubmitDialog
              sessionId={sessionId}
              events={events}
              onSubmitted={onSubmitted}
              triggerRef={submitTriggerRef}
              showShortcutHint
              isMac={isMac}
            />
          </div>
        </div>
      </div>

      {sandboxDriver === "local" ? (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-md border border-[oklch(from_var(--color-warning)_l_c_h/0.4)] bg-[oklch(from_var(--color-warning)_l_c_h/0.12)] px-3 py-1.5 text-[11px] text-[var(--color-foreground)]"
        >
          <AlertTriangle
            className="mt-0.5 size-3.5 shrink-0 text-[var(--color-warning)]"
            aria-hidden
          />
          <p>
            <strong>Local sandbox driver in use.</strong> No isolation — commands
            execute directly on the host. This mode is for laptops without
            Docker; never enable in production.
          </p>
        </div>
      ) : null}

      {open ? (
        <div
          id="process-signals-panel"
          className="absolute right-4 top-full z-30 mt-2 w-[360px] origin-top-right rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-elevated"
          style={{ animation: "fadeSlideIn 180ms var(--ease-macos, cubic-bezier(0.32, 0.72, 0, 1))" }}
        >
          <ScorePreview
            expectedRequiredContext={expectedRequiredContext}
            selectedContext={selectedContext}
            events={events}
            changedFiles={diffChangedFiles}
            className="border-none shadow-none"
          />
        </div>
      ) : null}
    </div>
  );
}

/**
 * P0-12 — overflow menu that surfaces the "Reset workspace" affordance
 * (and is the obvious home for future workspace-scoped actions like
 * keyboard help). Built as a small inline disclosure rather than
 * pulling in a Radix DropdownMenu — three items, neutral visual
 * weight, escape closes, click-outside closes. ARIA: role="menu" /
 * role="menuitem" with keyboard support via the dialog's own focus
 * trap when an item opens it.
 */
function WorkspaceOverflowMenu({ sessionId }: { sessionId: string }) {
  const [open, setOpen] = React.useState(false);
  const [resetOpen, setResetOpen] = React.useState(false);
  const menuRef = React.useRef<HTMLDivElement | null>(null);
  const buttonRef = React.useRef<HTMLButtonElement | null>(null);

  // Click-outside + escape close. We listen on mousedown so the close
  // happens before any other handler on the page (matches the
  // ScorePreview pop-out behaviour above).
  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      const target = e.target as Node | null;
      if (!target) return;
      if (menuRef.current?.contains(target)) return;
      if (buttonRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="More workspace actions"
        data-testid="workspace-overflow-trigger"
        className={cn(
          "inline-flex h-7 w-7 items-center justify-center rounded-md border border-[var(--color-border)] bg-[var(--color-surface)]",
          "text-[var(--color-muted-foreground)]",
          "transition-[background-color,color] duration-150 ease-macos hover:bg-[var(--color-muted)] hover:text-[var(--color-foreground)]",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-background)]",
          open && "bg-[var(--color-muted)] text-[var(--color-foreground)]",
        )}
      >
        <MoreHorizontal className="size-4" aria-hidden />
      </button>
      {open ? (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Workspace actions"
          className="absolute right-0 top-full z-30 mt-1 min-w-[200px] origin-top-right rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-1 shadow-elevated"
          style={{
            animation:
              "fadeSlideIn 160ms var(--ease-macos, cubic-bezier(0.32, 0.72, 0, 1))",
          }}
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              // ``session_reset_requested`` fires inside the dialog on
              // Confirm so the payload can include ``files_discarded_estimate``
              // and the signal isn't inflated by users who open the
              // overflow menu and back out without confirming.
              setOpen(false);
              setResetOpen(true);
            }}
            data-testid="reset-workspace-menuitem"
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-[var(--color-foreground)] transition-colors duration-150 ease-macos hover:bg-[var(--color-muted)] focus-visible:outline-none focus-visible:bg-[var(--color-muted)]"
          >
            <Undo2 className="size-3.5 text-[var(--color-muted-foreground)]" aria-hidden />
            <span>Reset workspace</span>
          </button>
        </div>
      ) : null}
      <ResetWorkspaceDialog
        sessionId={sessionId}
        open={resetOpen}
        onOpenChange={setResetOpen}
      />
    </div>
  );
}

/** Compact "ok signals / total" tally for the always-visible pill. */
function summarisePillSignals(
  expectedRequiredContext: string[],
  selectedContext: string[],
  events: SupervisionEvent[]
): { hit: number; total: number } {
  // Mirrors ScorePreview's 4 process signals.
  const selected = new Set(selectedContext);
  const requiredHit =
    expectedRequiredContext.length === 0 ||
    expectedRequiredContext.every((p) => selected.has(p));
  const ranVerification = events.some(
    (e) =>
      e.event_type === "command.run" &&
      (e.payload.category === "test" ||
        e.payload.category === "typecheck" ||
        e.payload.category === "lint")
  );
  const hasDiff = events.some((e) => e.event_type === "patch.applied");
  const diffOpened = events.some((e) => e.event_type === "diff.opened");

  const hit =
    Number(requiredHit) +
    Number(ranVerification) +
    Number(hasDiff) +
    Number(diffOpened);

  return { hit, total: 4 };
}
