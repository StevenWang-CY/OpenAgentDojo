"use client";

import * as React from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Edit3,
  FileCheck,
  FilePlus,
  GitMerge,
  ListChecks,
  MessageSquare,
  Terminal as TerminalIcon,
  Undo2,
  Wrench,
  XCircle,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { SupervisionEvent } from "@arena/shared-types";
import { ScrollArea } from "@/components/ui/ScrollArea";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

interface TimelineProps {
  events: SupervisionEvent[];
  className?: string;
}

type Tone = "neutral" | "primary" | "success" | "warning" | "danger";

interface RenderedEvent {
  icon: LucideIcon;
  tone: Tone;
  label: string;
  detail: string;
  /** Optional non-empty breakdown chart for submission.graded. */
  breakdown?: Array<{ key: string; value: number }>;
}

/**
 * Compile-time exhaustiveness assertion. If a new `event_type` is added to
 * the discriminated union but missing a `case` below, the call to `unreachable`
 * will fail to type-check (because the parameter would no longer be `never`).
 */
function unreachable(value: never): RenderedEvent {
  return {
    icon: Circle,
    tone: "neutral",
    label: (value as { event_type?: string }).event_type ?? "unknown",
    detail: "",
  };
}

function render(event: SupervisionEvent): RenderedEvent {
  switch (event.event_type) {
    case "session.started":
      return {
        icon: Circle,
        tone: "primary",
        label: "Session started",
        detail: event.payload.initial_commit
          ? `Commit ${event.payload.initial_commit.slice(0, 8)}`
          : "Sandbox provisioning",
      };
    case "session.errored":
      return {
        icon: XCircle,
        tone: "danger",
        label: "Session errored",
        detail: `${event.payload.stage}: ${event.payload.detail}`,
      };
    case "session.abandoned":
      return {
        icon: AlertTriangle,
        tone: "warning",
        label: "Session abandoned",
        detail: event.payload.reason ?? "Session reaped",
      };
    case "context.selected":
      return {
        icon: ListChecks,
        tone: "neutral",
        label: "Context updated",
        detail: `${(event.payload.files ?? []).length} files`,
      };
    case "prompt.submitted":
      return {
        icon: MessageSquare,
        tone: "primary",
        label: `Prompt #${event.payload.turn_index + 1}`,
        detail: `${truncate(event.payload.text, 80)} (${event.payload.char_count} chars)`,
      };
    case "agent.responded":
      return {
        icon: Wrench,
        tone: "neutral",
        label: `Agent responded #${event.payload.turn_index + 1}`,
        detail: truncate(event.payload.response_summary, 80),
      };
    case "patch.proposed":
      return {
        icon: GitMerge,
        tone: "primary",
        label: `Patch proposed #${event.payload.turn_index + 1}`,
        detail: event.payload.patch_file,
      };
    case "patch.applied":
      return {
        icon: GitMerge,
        tone: "warning",
        label: "Patch applied",
        detail: `${event.payload.files_changed} files · +${event.payload.added} / -${event.payload.removed}`,
      };
    case "patch.failed": {
      const counts =
        event.payload.files_changed !== undefined
          ? ` (${event.payload.files_changed} files · +${event.payload.added ?? 0} / -${event.payload.removed ?? 0})`
          : "";
      return {
        icon: XCircle,
        tone: "danger",
        label: "Patch failed",
        detail: `${event.payload.error}${counts}`,
      };
    }
    case "diff.opened":
      return {
        icon: FileCheck,
        tone: "neutral",
        label: "Diff opened",
        detail: event.payload.path === "" ? "(workspace)" : event.payload.path,
      };
    case "diff.hovered":
      return {
        icon: FileCheck,
        tone: "neutral",
        label: "Diff hovered",
        detail: `${event.payload.path}:${event.payload.line ?? "?"}`,
      };
    case "file.edited": {
      const who = event.payload.source ? ` [${event.payload.source}]` : "";
      return {
        icon: Edit3,
        tone: "neutral",
        label: "File edited",
        detail: `${event.payload.path} (+${event.payload.added} / -${event.payload.removed})${who}`,
      };
    }
    case "file.reverted":
      return {
        icon: Undo2,
        tone: "neutral",
        label: "File reverted",
        detail: event.payload.path,
      };
    case "command.run": {
      const exit = event.payload.exit_code;
      const ok = exit === 0;
      const exitFragment = exit === undefined ? "" : ` (exit ${exit})`;
      return {
        icon: TerminalIcon,
        tone: exit === undefined ? "neutral" : ok ? "success" : "danger",
        label: `Command · ${event.payload.category}`,
        detail: `$ ${truncate(event.payload.command, 80)}${exitFragment}`,
      };
    }
    case "test.run": {
      const tone: Tone = event.payload.failed > 0 ? "danger" : "success";
      const skippedFragment =
        event.payload.skipped !== undefined
          ? `, ${event.payload.skipped} skipped`
          : "";
      return {
        icon: CheckCircle2,
        tone,
        label: `Tests · ${event.payload.suite}`,
        detail: `${event.payload.passed} passed, ${event.payload.failed} failed${skippedFragment}`,
      };
    }
    case "validator.flag": {
      const penalty =
        event.payload.penalty !== undefined
          ? ` (−${event.payload.penalty})`
          : "";
      return {
        icon: AlertTriangle,
        tone: "warning",
        label: `Validator · ${event.payload.kind}`,
        detail: `${event.payload.message}${penalty}`,
      };
    }
    case "submission.requested":
      return {
        icon: FilePlus,
        tone: "primary",
        label: "Submission requested",
        detail: "Grading run started…",
      };
    case "submission.graded": {
      const entries = Object.entries(event.payload.breakdown ?? {});
      return {
        icon: CheckCircle2,
        tone: "success",
        label: "Submission graded",
        detail: `Total ${event.payload.score} / 100`,
        breakdown:
          entries.length > 0
            ? entries.map(([key, dim]) => ({
                key,
                // Older payloads (legacy submission.graded events emitted
                // before the rubric envelope landed) shipped a bare number
                // here; the new contract is a full `ScoreDimension`. Read
                // ``score`` defensively so the timeline keeps rendering.
                value:
                  typeof dim === "number"
                    ? dim
                    : typeof dim?.score === "number"
                      ? dim.score
                      : 0,
              }))
            : undefined,
      };
    }
    case "submission.failed":
      return {
        icon: XCircle,
        tone: "danger",
        label: "Submission failed",
        detail: `${event.payload.stage}: ${event.payload.detail}`,
      };
    default:
      return unreachable(event);
  }
}

interface MinuteGroup {
  bucket: string;
  events: SupervisionEvent[];
}

/**
 * Bucket events into HH:mm groups *in occurrence order*. A new bucket starts
 * whenever the rendered minute changes — so a session that spans minute
 * boundaries shows its events under each minute header in sequence, never
 * collapsed back into a single per-minute group.
 */
function groupByMinute(events: SupervisionEvent[]): MinuteGroup[] {
  if (events.length === 0) return [];
  const groups: MinuteGroup[] = [];
  let current: MinuteGroup | null = null;
  for (const event of events) {
    const bucket = formatMinuteBucket(event.occurred_at);
    if (!current || current.bucket !== bucket) {
      current = { bucket, events: [event] };
      groups.push(current);
    } else {
      current.events.push(event);
    }
  }
  return groups;
}

function formatMinuteBucket(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--";
  return d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function Timeline({ events, className }: TimelineProps) {
  if (events.length === 0) {
    return (
      <div
        className={cn(
          "flex h-full items-center justify-center p-4 text-xs text-[var(--color-muted-foreground)]",
          className
        )}
      >
        Your supervision timeline will appear here as you work.
      </div>
    );
  }

  const groups = groupByMinute(events);

  return (
    <ScrollArea className={cn("h-full", className)}>
      <ol className="p-3">
        {groups.map((group, gi) => (
          <li key={`${group.bucket}-${gi}`} className="mb-3 last:mb-0">
            <h4
              className="mb-1 font-mono text-[10px] uppercase tracking-wider text-[var(--color-muted-foreground)]"
              data-testid="timeline-minute"
            >
              {group.bucket}
            </h4>
            <ul className="space-y-1">
              {group.events.map((event) => {
                const r = render(event);
                const Icon = r.icon;
                return (
                  <li
                    key={`${event.id}-${event.occurred_at}`}
                    className="flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-[var(--color-muted)]"
                  >
                    <span
                      aria-hidden
                      className={cn(
                        "mt-0.5 grid size-5 shrink-0 place-items-center rounded-full",
                        toneClass(r.tone)
                      )}
                    >
                      <Icon className="size-3" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline justify-between gap-2">
                        <p className="truncate text-xs font-medium">{r.label}</p>
                        <time className="font-mono text-[10px] text-[var(--color-muted-foreground)]">
                          {formatRelative(event.occurred_at)}
                        </time>
                      </div>
                      {r.detail ? (
                        <p className="mt-0.5 truncate font-mono text-[11px] text-[var(--color-muted-foreground)]">
                          {r.detail}
                        </p>
                      ) : null}
                      {r.breakdown && r.breakdown.length > 0 ? (
                        <BreakdownChart entries={r.breakdown} />
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          </li>
        ))}
      </ol>
    </ScrollArea>
  );
}

function BreakdownChart({
  entries,
}: {
  entries: Array<{ key: string; value: number }>;
}) {
  const max = Math.max(1, ...entries.map((e) => e.value));
  return (
    <ul
      className="mt-1 space-y-0.5"
      aria-label="Score breakdown by dimension"
      data-testid="timeline-breakdown"
    >
      {entries.map(({ key, value }) => (
        <li
          key={key}
          className="flex items-center gap-2 font-mono text-[10px] text-[var(--color-muted-foreground)]"
        >
          <span className="w-32 shrink-0 truncate" title={key}>
            {key}
          </span>
          <span
            aria-hidden
            className="h-1 flex-1 overflow-hidden rounded-full bg-[var(--color-muted)]"
          >
            <span
              className="block h-full bg-[var(--color-primary)]"
              style={{ width: `${Math.min(100, (value / max) * 100)}%` }}
            />
          </span>
          <span className="w-8 shrink-0 text-right tabular-nums">{value}</span>
        </li>
      ))}
    </ul>
  );
}

function toneClass(tone: Tone): string {
  switch (tone) {
    case "primary":
      return "bg-[oklch(from_var(--color-primary)_l_c_h/0.18)] text-[var(--color-primary)]";
    case "success":
      return "bg-[oklch(from_var(--color-success)_l_c_h/0.18)] text-[var(--color-success)]";
    case "warning":
      return "bg-[oklch(from_var(--color-warning)_l_c_h/0.18)] text-[var(--color-warning)]";
    case "danger":
      return "bg-[oklch(from_var(--color-danger)_l_c_h/0.18)] text-[var(--color-danger)]";
    case "neutral":
    default:
      return "bg-[var(--color-muted)] text-[var(--color-muted-foreground)]";
  }
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}
