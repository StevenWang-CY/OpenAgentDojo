"use client";

import * as React from "react";
import { Check, Gauge, Minus, X } from "lucide-react";
import type { SupervisionEvent } from "@arena/shared-types";
import { cn } from "@/lib/utils";

interface ScorePreviewProps {
  /**
   * Files the user has marked as required for the current mission.
   * Drives the "context X of Y" indicator without leaking the discouraged set.
   */
  expectedRequiredContext: string[];
  /** Currently-selected context paths. */
  selectedContext: string[];
  /** All supervision events for this session — used to compute process signals. */
  events: SupervisionEvent[];
  /**
   * Files the user is currently *off-scope* for. Optional, used to show a
   * "minimality" hint. Pass the union of changed files.
   */
  changedFiles?: string[];
  className?: string;
}

interface Signal {
  label: string;
  status: "ok" | "warn" | "todo";
  hint?: string;
}

/**
 * Process-only score preview per IMPLEMENTATION_PLAN.md §13.5 — never leaks
 * hidden test outcomes or predicts a total score.
 */
export function ScorePreview({
  expectedRequiredContext,
  selectedContext,
  events,
  changedFiles,
  className,
}: ScorePreviewProps) {
  const signals = React.useMemo<Signal[]>(() => {
    const out: Signal[] = [];

    // Context selection.
    const required = new Set(expectedRequiredContext);
    const selected = new Set(selectedContext);
    const hit = expectedRequiredContext.filter((p) => selected.has(p)).length;
    out.push({
      label: `Context: ${hit}/${required.size} required selected`,
      status:
        required.size === 0 ? "ok" : hit === required.size ? "ok" : hit > 0 ? "warn" : "todo",
      hint:
        required.size > 0 && hit < required.size
          ? "Open the file tree and tick the boxes for files the agent should read."
          : undefined,
    });

    // Verification — did the user run any test/typecheck command that
    // actually *passed*? A command exiting 127 (e.g. ``pnpm`` missing in a
    // Go/Python sandbox) or otherwise failing earns no credit, mirroring
    // the backend grader's ``exit_code == 0`` gate.
    const testRuns = events.filter(
      (e) =>
        e.event_type === "command.run" &&
        (e.payload.category === "test" ||
          e.payload.category === "typecheck" ||
          e.payload.category === "lint") &&
        e.payload.exit_code === 0
    );
    out.push({
      label:
        testRuns.length === 0
          ? "Verification: tests not yet run"
          : `Verification: ${testRuns.length} check${testRuns.length === 1 ? "" : "s"} run`,
      status: testRuns.length === 0 ? "todo" : "ok",
    });

    // Diff scope hint.
    const filesChanged = changedFiles?.length ?? 0;
    out.push({
      label: `Diff: ${filesChanged} file${filesChanged === 1 ? "" : "s"} changed`,
      status: filesChanged === 0 ? "todo" : "ok",
    });

    // Diff review.
    const diffOpened = events.some((e) => e.event_type === "diff.opened");
    out.push({
      label: diffOpened ? "Agent review: diff opened" : "Agent review: open the diff",
      status: diffOpened ? "ok" : "todo",
    });

    return out;
  }, [expectedRequiredContext, selectedContext, events, changedFiles]);

  return (
    <div className={cn("rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 shadow-soft", className)}>
      <div className="flex items-center gap-2">
        <Gauge className="size-4 text-[var(--color-primary)]" aria-hidden />
        <h3 className="text-sm font-semibold tracking-tight">Process signals</h3>
      </div>
      <p className="mt-1 text-[11px] text-[var(--color-muted-foreground)]">
        Live indicators on the <em>process</em> dimensions. Hidden tests stay
        hidden until you submit.
      </p>
      <ul className="mt-3 space-y-2 text-xs">
        {signals.map((s) => (
          <li key={s.label} className="flex items-start gap-2">
            <span aria-hidden className={cn("mt-0.5 shrink-0", iconColor(s.status))}>
              {s.status === "ok" ? (
                <Check className="size-3.5" />
              ) : s.status === "warn" ? (
                <Minus className="size-3.5" />
              ) : (
                <X className="size-3.5" />
              )}
            </span>
            <div>
              <p className="text-[var(--color-foreground)]">{s.label}</p>
              {s.hint ? (
                <p className="text-[11px] text-[var(--color-muted-foreground)]">
                  {s.hint}
                </p>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function iconColor(status: Signal["status"]): string {
  if (status === "ok") return "text-[var(--color-success)]";
  if (status === "warn") return "text-[var(--color-warning)]";
  return "text-[var(--color-muted-foreground)]";
}
