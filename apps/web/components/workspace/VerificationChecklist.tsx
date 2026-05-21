"use client";

import * as React from "react";
import { CheckCircle2, Circle } from "lucide-react";
import type { SupervisionEvent } from "@arena/shared-types";
import { cn } from "@/lib/utils";

interface VerificationChecklistProps {
  events: SupervisionEvent[];
  className?: string;
}

interface ChecklistItem {
  id: string;
  label: string;
  predicate(events: SupervisionEvent[]): boolean;
}

/**
 * A small, opinionated checklist that surfaces inside the SubmitDialog. It's
 * cross-referenced against the supervision event stream so users can't fake
 * progress just by ticking boxes.
 */
const ITEMS: ChecklistItem[] = [
  {
    id: "diff_opened",
    label: "I opened the diff after the agent applied a patch.",
    predicate: (e) => e.some((ev) => ev.event_type === "diff.opened"),
  },
  {
    id: "ran_test",
    label: "I ran the test suite at least once.",
    predicate: (e) =>
      e.some(
        (ev) =>
          ev.event_type === "command.run" && ev.payload.category === "test"
      ),
  },
  {
    id: "ran_typecheck",
    label: "I ran typecheck or lint.",
    predicate: (e) =>
      e.some(
        (ev) =>
          ev.event_type === "command.run" &&
          (ev.payload.category === "typecheck" || ev.payload.category === "lint")
      ),
  },
  {
    id: "made_edit_or_corrective_prompt",
    label: "I corrected the agent (edited a file or sent a follow-up prompt).",
    predicate: (e) =>
      e.some(
        (ev) =>
          ev.event_type === "file.edited" ||
          ev.event_type === "file.reverted" ||
          (ev.event_type === "prompt.submitted" && ev.payload.turn_index > 0)
      ),
  },
];

export function VerificationChecklist({ events, className }: VerificationChecklistProps) {
  const completed = React.useMemo(
    () => ITEMS.map((item) => ({ ...item, done: item.predicate(events) })),
    [events]
  );

  return (
    <ul className={cn("space-y-2 text-sm", className)} aria-label="Pre-submit checklist">
      {completed.map((item) => (
        <li key={item.id} className="flex items-start gap-2">
          {item.done ? (
            <CheckCircle2
              className="mt-0.5 size-4 shrink-0 text-[var(--color-success)]"
              aria-hidden
            />
          ) : (
            <Circle
              className="mt-0.5 size-4 shrink-0 text-[var(--color-muted-foreground)]"
              aria-hidden
            />
          )}
          <span
            className={cn(
              item.done
                ? "text-[var(--color-foreground)]"
                : "text-[var(--color-muted-foreground)]"
            )}
          >
            {item.label}
          </span>
        </li>
      ))}
    </ul>
  );
}
