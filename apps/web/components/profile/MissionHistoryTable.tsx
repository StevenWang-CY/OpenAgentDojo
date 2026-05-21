"use client";

import * as React from "react";
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
      <div className="rounded-xl border border-dashed border-[var(--color-border)] p-6 text-center text-sm text-[var(--color-muted-foreground)]">
        No mission history yet.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-soft">
      <table className="min-w-full text-sm">
        <thead className="border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)] text-left text-xs uppercase tracking-wide text-[var(--color-muted-foreground)]">
          <tr>
            <th className="px-4 py-2 font-medium">Mission</th>
            <th className="px-4 py-2 font-medium">Difficulty</th>
            <th className="px-4 py-2 font-medium">Completed</th>
            <th className="px-4 py-2 text-right font-medium">Score</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const href = `/missions/${item.mission_id}`;
            return (
              // The row itself is a real <tr> (not a synthetic link) — we
              // instead expand a single inner anchor to fill the row via
              // `before:absolute before:inset-0`, so screen readers see one
              // semantically correct link per row, native middle-click /
              // open-in-new-tab work, and we don't duplicate events.
              <tr
                key={item.session_id}
                className="group border-b border-[var(--color-border)] last:border-b-0 transition-colors duration-150 ease-macos hover:bg-[var(--color-muted)] focus-within:bg-[var(--color-muted)]"
              >
                <td className="relative px-4 py-2">
                  <Link
                    href={href}
                    className="relative font-medium underline-offset-2 hover:underline before:absolute before:inset-0 before:content-[''] focus-visible:outline-none focus-visible:before:rounded-sm focus-visible:before:ring-2 focus-visible:before:ring-[var(--color-ring)]"
                  >
                    {item.mission_title}
                  </Link>
                </td>
                <td className="relative px-4 py-2">
                  <DifficultyBadge difficulty={item.difficulty} />
                </td>
                <td className="relative px-4 py-2 text-xs text-[var(--color-muted-foreground)]">
                  {item.completed_at ? formatDateTime(item.completed_at) : "—"}
                </td>
                <td className="relative px-4 py-2 text-right font-mono">
                  {item.score ?? "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
