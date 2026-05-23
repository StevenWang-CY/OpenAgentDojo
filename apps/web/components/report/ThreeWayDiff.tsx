"use client";

import * as React from "react";
import { GitMerge } from "lucide-react";
import { DiffViewer } from "@/components/workspace/DiffViewer";
import { cn } from "@/lib/utils";

interface ThreeWayDiffProps {
  /** The diff the user submitted (vs. initial commit). */
  userDiff: string;
  /** The canonical fix, vs. initial commit. */
  idealDiff: string;
  /** The agent's original patch (deliberately flawed). */
  agentPatchDiff: string | null;
  className?: string;
}

/**
 * P0-2 — three-way diff comparison.
 *
 * Two primary panes side by side (user vs. ideal) + an expandable
 * third strip below (agent's original patch). The third pane is
 * collapsed by default because it's the least load-bearing of the
 * three: by the time the report renders, the user has presumably
 * already seen the agent's diff in the workspace.
 *
 * Falls back to a single-pane view when ideal/user are absent (e.g.
 * tutorial missions don't ship ideal_solution.diff).
 */
export function ThreeWayDiff({
  userDiff,
  idealDiff,
  agentPatchDiff,
  className,
}: ThreeWayDiffProps) {
  const [showAgent, setShowAgent] = React.useState(false);

  const haveUser = userDiff.trim().length > 0;
  const haveIdeal = idealDiff.trim().length > 0;

  if (!haveUser && !haveIdeal) {
    return null;
  }

  return (
    <div className={cn("grid gap-4", className)} data-testid="three-way-diff">
      <div className="grid gap-4 lg:grid-cols-2">
        <DiffLayer
          label="you submitted"
          tone="user"
          diff={haveUser ? userDiff : ""}
          emptyLabel="No changes submitted."
        />
        <DiffLayer
          label="ideal solution"
          tone="ideal"
          diff={haveIdeal ? idealDiff : ""}
          emptyLabel="No canonical fix shipped for this mission."
        />
      </div>
      {agentPatchDiff ? (
        <details
          className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]"
          onToggle={(e) => setShowAgent((e.target as HTMLDetailsElement).open)}
        >
          <summary className="flex cursor-pointer items-center gap-2 px-4 py-2.5 font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
            <GitMerge className="size-3.5" aria-hidden />
            {"// agent's original patch"}
            <span className="ml-2 text-[10px] normal-case tracking-normal text-[var(--color-muted-foreground)]/70">
              {showAgent ? "(hide)" : "(show)"}
            </span>
          </summary>
          <div className="border-t border-[var(--color-border)] p-3">
            <DiffViewer
              unifiedDiff={agentPatchDiff}
              defaultViewType="unified"
            />
          </div>
        </details>
      ) : null}
    </div>
  );
}

function DiffLayer({
  label,
  tone,
  diff,
  emptyLabel,
}: {
  label: string;
  tone: "user" | "ideal";
  diff: string;
  emptyLabel: string;
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <header
        className={cn(
          "flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2 font-mono text-[10px] uppercase tracking-[0.18em]",
          tone === "ideal"
            ? "bg-[oklch(from_var(--color-success)_l_c_h/0.08)] text-[var(--color-success)]"
            : "bg-[var(--color-muted)] text-[var(--color-muted-foreground)]",
        )}
      >
        <span>{"// "}{label}</span>
      </header>
      <div className="p-3">
        {diff ? (
          <DiffViewer unifiedDiff={diff} defaultViewType="unified" />
        ) : (
          <p className="px-2 py-6 text-center text-sm text-[var(--color-muted-foreground)]">
            {emptyLabel}
          </p>
        )}
      </div>
    </div>
  );
}
