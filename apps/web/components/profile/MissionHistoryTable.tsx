"use client";

import Link from "next/link";
import type { MissionHistoryItem } from "@arena/shared-types";
import { DifficultyBadge } from "@/components/catalog/DifficultyBadge";
import { formatDateTime } from "@/lib/format";

interface MissionHistoryTableProps {
  items: MissionHistoryItem[];
}

export function MissionHistoryTable({ items }: MissionHistoryTableProps) {
  if (items.length === 0) {
    return (
      <p className="mt-4 rounded-lg border border-dashed border-[var(--color-border)] py-6 text-center font-mono text-xs text-[var(--color-muted-foreground)]">
        {"// no mission history yet."}
      </p>
    );
  }
  return (
    <div className="mt-4 overflow-hidden rounded-lg border border-[var(--color-border)]">
      <div
        className="grid items-center gap-4 border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-4 py-2.5 font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--color-muted-foreground)] sm:px-5"
        style={{
          gridTemplateColumns:
            "minmax(0,1.6fr) 110px minmax(0,120px) 80px 24px",
        }}
      >
        <span>mission</span>
        <span>level</span>
        <span>completed</span>
        <span className="text-right">score</span>
        <span></span>
      </div>
      {items.map((item, idx) => (
        <Link
          key={item.session_id}
          href={`/missions/${item.mission_id}`}
          className="group grid items-center gap-4 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3.5 transition-colors duration-150 ease-macos last:border-b-0 hover:bg-[var(--color-muted)] sm:px-5"
          style={{
            gridTemplateColumns:
              "minmax(0,1.6fr) 110px minmax(0,120px) 80px 24px",
          }}
          data-testid={idx === 0 ? "mission-history-row-first" : undefined}
        >
          <div className="min-w-0">
            <p className="truncate text-sm font-medium leading-tight">
              {item.mission_title}
            </p>
            <p className="mt-0.5 truncate font-mono text-[11px] text-[var(--color-muted-foreground)]">
              {item.mission_id}
            </p>
          </div>
          <div>
            <DifficultyBadge difficulty={item.difficulty} />
          </div>
          <p className="truncate font-mono text-[11px] text-[var(--color-muted-foreground)]">
            {item.completed_at ? formatDateTime(item.completed_at) : "—"}
          </p>
          <p className="text-right font-mono text-sm font-semibold tabular-nums">
            {item.score ?? "—"}
          </p>
          <span
            aria-hidden
            className="text-right text-[var(--color-muted-foreground)] transition-[transform,color] duration-150 group-hover:translate-x-0.5 group-hover:text-[var(--color-foreground)]"
          >
            →
          </span>
        </Link>
      ))}
    </div>
  );
}
