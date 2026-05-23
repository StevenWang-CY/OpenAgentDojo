"use client";

import * as React from "react";
import type { SupervisionEvent } from "@arena/shared-types";
import { Timeline } from "@/components/workspace/Timeline";

interface TimelineReplayProps {
  events: SupervisionEvent[];
  /** P0-2 — when set, the replay seeks to this event id and pulses it
   *  for a moment. The parent (ReportView) wires this to evidence chip
   *  + critical-moment scrubber clicks. */
  highlightEventId?: number | null;
}

/**
 * Read-only replay of a supervision event stream — for the post-mission
 * report. Adds a scrubber that filters events by occurred_at threshold.
 *
 * P0-2 — supports scroll-to-event + transient pulse highlighting so the
 * post-mortem walkthrough can pin attention to a specific moment.
 */
export function TimelineReplay({
  events,
  highlightEventId = null,
}: TimelineReplayProps) {
  const [cursor, setCursor] = React.useState(events.length);
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const [pulse, setPulse] = React.useState<number | null>(null);

  React.useEffect(() => {
    setCursor(events.length);
  }, [events.length]);

  // Keep the events array in a ref so the highlight effect doesn't have
  // to depend on it. Without this, every cursor-slider tick re-fires the
  // scroll-into-view and the pulse animation restarts mid-cycle.
  const eventsRef = React.useRef(events);
  React.useEffect(() => {
    eventsRef.current = events;
  }, [events]);

  // When highlightEventId changes, ensure the cursor advances enough to
  // include the event, scroll it into view, and trigger a brief pulse.
  React.useEffect(() => {
    if (highlightEventId == null) return;
    const evs = eventsRef.current;
    const idx = evs.findIndex((e) => e.id === highlightEventId);
    if (idx === -1) return;
    setCursor((c) => Math.max(c, idx + 1));
    setPulse(highlightEventId);

    const frame = requestAnimationFrame(() => {
      const node = containerRef.current?.querySelector<HTMLElement>(
        `[data-event-id='${highlightEventId}']`,
      );
      if (node) {
        node.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    });

    const clear = window.setTimeout(() => setPulse(null), 1500);
    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(clear);
    };
  }, [highlightEventId]);

  const visible = events.slice(0, cursor);

  return (
    <div className="flex flex-col gap-3">
      <label className="flex items-center gap-2 text-xs text-[var(--color-muted-foreground)]">
        <span>Replay cursor</span>
        <input
          type="range"
          min={0}
          max={events.length}
          value={cursor}
          onChange={(e) => setCursor(Number(e.target.value))}
          className="flex-1 accent-[var(--color-primary)]"
          aria-label="Replay cursor"
        />
        <span className="font-mono">
          {visible.length} / {events.length}
        </span>
      </label>
      <div
        ref={containerRef}
        className="h-64 overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]"
        data-pulse-event-id={pulse ?? undefined}
      >
        <Timeline events={visible} pulseEventId={pulse ?? undefined} />
      </div>
    </div>
  );
}
