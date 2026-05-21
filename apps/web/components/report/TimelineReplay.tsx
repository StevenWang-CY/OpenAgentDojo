"use client";

import * as React from "react";
import type { SupervisionEvent } from "@arena/shared-types";
import { Timeline } from "@/components/workspace/Timeline";

interface TimelineReplayProps {
  events: SupervisionEvent[];
}

/**
 * Read-only replay of a supervision event stream — for the post-mission
 * report. Adds a scrubber that filters events by occurred_at threshold.
 */
export function TimelineReplay({ events }: TimelineReplayProps) {
  const [cursor, setCursor] = React.useState(events.length);

  React.useEffect(() => {
    setCursor(events.length);
  }, [events.length]);

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
      <div className="h-64 overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]">
        <Timeline events={visible} />
      </div>
    </div>
  );
}
