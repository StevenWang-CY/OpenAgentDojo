"use client";

import * as React from "react";
import { AlertTriangle, Clock, ChevronLeft, ChevronRight } from "lucide-react";
import type { CriticalMoment, SupervisionEvent } from "@arena/shared-types";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

interface CriticalMomentScrubberProps {
  moments: CriticalMoment[];
  events: SupervisionEvent[];
  onScrollToEvent?: (eventId: number) => void;
}

/**
 * P0-2 — surfaces the critical moments at the top of the report.
 *
 * Each moment pins to an exact supervision-event id; hovering or
 * focusing one calls ``onScrollToEvent`` so the TimelineReplay below
 * scrolls + pulses the corresponding event. Up to three moments are
 * shown; an arrow strip lets the user step through them sequentially.
 */
export function CriticalMomentScrubber({
  moments,
  events,
  onScrollToEvent,
}: CriticalMomentScrubberProps) {
  const [activeIndex, setActiveIndex] = React.useState(0);
  if (moments.length === 0) return null;

  const active = moments[Math.min(activeIndex, moments.length - 1)];
  if (!active) return null;

  const surrounding = surroundingEvents(events, active.event_id, 2);

  function goPrev() {
    setActiveIndex((i) => Math.max(0, i - 1));
  }
  function goNext() {
    setActiveIndex((i) => Math.min(moments.length - 1, i + 1));
  }

  return (
    <section
      aria-label="Critical moment"
      data-testid="critical-moment-scrubber"
      className="grid gap-3"
    >
      <article className="rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-surface)] p-4 shadow-sm">
        <header className="flex flex-wrap items-baseline justify-between gap-3">
          <span className="inline-flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--color-danger)]">
            <AlertTriangle className="size-3.5" aria-hidden />
            critical moment {activeIndex + 1} / {moments.length}
          </span>
          {active.occurred_at ? (
            <span className="inline-flex items-center gap-1 font-mono text-[11px] tabular-nums text-[var(--color-muted-foreground)]">
              <Clock className="size-3" aria-hidden />
              {formatTimeOfSession(active.occurred_at, events)}
            </span>
          ) : null}
        </header>
        <h3 className="mt-2 text-sm font-semibold leading-snug">
          {labelForKind(active.kind)}
        </h3>
        <p className="mt-1.5 text-sm leading-relaxed">{active.explanation}</p>
        <p className="mt-2 text-sm leading-relaxed text-[var(--color-muted-foreground)]">
          <span className="font-medium text-[var(--color-foreground)]">
            What to do instead:{" "}
          </span>
          {active.what_to_do_instead}
        </p>
        <div className="mt-3 flex items-center justify-between gap-2">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={activeIndex <= 0}
            onClick={goPrev}
            aria-label="Previous moment"
          >
            <ChevronLeft className="size-3.5" aria-hidden />
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => onScrollToEvent?.(active.event_id)}
            data-testid="critical-moment-scroll"
          >
            Show in timeline →
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={activeIndex >= moments.length - 1}
            onClick={goNext}
            aria-label="Next moment"
          >
            <ChevronRight className="size-3.5" aria-hidden />
          </Button>
        </div>
      </article>
      {surrounding.length > 0 ? (
        <ol className="grid grid-cols-1 gap-1 font-mono text-[11px] sm:grid-cols-3">
          {surrounding.map((ev) => (
            <li
              key={ev.id}
              className={cn(
                "rounded border border-[var(--color-border)] px-2 py-1.5 transition-colors duration-150",
                ev.id === active.event_id
                  ? "border-[var(--color-danger)] bg-[oklch(from_var(--color-danger)_l_c_h/0.08)]"
                  : "bg-[var(--color-surface)]",
              )}
            >
              <span className="text-[var(--color-muted-foreground)]">
                #{ev.id}
              </span>{" "}
              <span className="text-[var(--color-foreground)]">
                {ev.event_type}
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}

function labelForKind(kind: CriticalMoment["kind"]): string {
  switch (kind) {
    case "agent_responded_no_review":
      return "You skipped the diff after the agent's last patch";
    case "submitted_without_verification":
      return "You submitted without running any tests";
    case "wrong_layer_committed":
      return "A validator flagged a forbidden change you submitted anyway";
    case "missed_corrective_window":
      return "You submitted within 15 seconds of the agent responding";
    default:
      return "Critical moment";
  }
}

function surroundingEvents(
  events: SupervisionEvent[],
  anchorId: number,
  span: number,
): SupervisionEvent[] {
  const idx = events.findIndex((e) => e.id === anchorId);
  if (idx === -1) return [];
  const start = Math.max(0, idx - span);
  const end = Math.min(events.length, idx + span + 1);
  return events.slice(start, end);
}

function formatTimeOfSession(
  iso: string,
  events: SupervisionEvent[],
): string {
  const first = events[0]?.occurred_at;
  if (!first) {
    try {
      return new Date(iso).toLocaleTimeString();
    } catch {
      return iso;
    }
  }
  try {
    const delta = (new Date(iso).getTime() - new Date(first).getTime()) / 1000;
    if (Number.isNaN(delta) || delta < 0) return iso;
    const minutes = Math.floor(delta / 60);
    const seconds = Math.floor(delta % 60);
    return `${minutes.toString().padStart(2, "0")}:${seconds
      .toString()
      .padStart(2, "0")}`;
  } catch {
    return iso;
  }
}
