"use client";

import * as React from "react";
import type { EvidenceEntry, StrengthOrString } from "@arena/shared-types";
import { asEvidenceEntry } from "@arena/shared-types";
import { cn } from "@/lib/utils";

interface DimensionEvidenceProps {
  /** Tone discriminator — picks the colour for the strip's left rule. */
  tone: "ok" | "bad";
  /** Either a raw string (legacy) or an EvidenceEntry (P0-2). */
  entries: StrengthOrString[];
  /** Fired when the user clicks an event chip; the parent scrolls the
   *  TimelineReplay to the corresponding event. */
  onScrollToEvent?: (eventId: number) => void;
}

/**
 * P0-2 — renders an evidence-bearing strength or weakness list.
 *
 * Each entry shows the dimension message and, when evidence ids are
 * available, a row of "→ events #N" buttons that scroll the timeline
 * to the corresponding supervision event. Legacy ``string`` entries
 * render without buttons (they're un-clickable by design — the FE
 * never invents evidence ids for them).
 */
export function DimensionEvidence({
  tone,
  entries,
  onScrollToEvent,
}: DimensionEvidenceProps) {
  if (entries.length === 0) return null;
  return (
    <ul
      className="grid gap-2"
      data-testid={tone === "ok" ? "strength-list" : "weakness-list"}
    >
      {entries.map((raw, idx) => {
        const entry: EvidenceEntry = asEvidenceEntry(raw);
        return (
          <li
            key={`${entry.dimension}-${idx}`}
            className={cn(
              "rounded-md border-l-2 bg-[var(--color-surface)] py-2 pl-3 pr-2",
              tone === "ok"
                ? "border-[var(--color-success)]"
                : "border-[var(--color-danger)]",
            )}
          >
            <p className="text-sm">
              <span
                aria-hidden
                className={cn(
                  "mr-2 font-mono font-semibold",
                  tone === "ok"
                    ? "text-[var(--color-success)]"
                    : "text-[var(--color-danger)]",
                )}
              >
                {tone === "ok" ? "✓" : "✕"}
              </span>
              {entry.message}
            </p>
            {entry.evidence_event_ids.length > 0 ? (
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
                  evidence →
                </span>
                {entry.evidence_event_ids.slice(0, 6).map((eid) => (
                  <button
                    key={eid}
                    type="button"
                    onClick={() => onScrollToEvent?.(eid)}
                    className="rounded border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-foreground)] transition-colors duration-150 ease-macos hover:bg-[var(--color-muted)]"
                    data-testid="evidence-event-link"
                    data-event-id={eid}
                  >
                    #{eid}
                  </button>
                ))}
                {entry.evidence_event_ids.length > 6 ? (
                  <span className="font-mono text-[10px] text-[var(--color-muted-foreground)]">
                    +{entry.evidence_event_ids.length - 6} more
                  </span>
                ) : null}
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
