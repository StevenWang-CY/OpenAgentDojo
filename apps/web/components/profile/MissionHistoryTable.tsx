"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { MissionHistoryItem } from "@arena/shared-types";
import { DifficultyBadge } from "@/components/catalog/DifficultyBadge";
import { formatDateTime } from "@/lib/format";

interface MissionHistoryTableProps {
  items: MissionHistoryItem[];
}

export function MissionHistoryTable({ items }: MissionHistoryTableProps) {
  const router = useRouter();

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
              <tr
                key={item.session_id}
                role="link"
                tabIndex={0}
                aria-label={`Open ${item.mission_title}`}
                onClick={() => router.push(href)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    router.push(href);
                  }
                }}
                className="cursor-pointer border-b border-[var(--color-border)] last:border-b-0 transition-colors duration-150 ease-macos hover:bg-[var(--color-muted)] focus-visible:bg-[var(--color-muted)] focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--color-ring)]"
              >
                <td className="px-4 py-2">
                  {/* Keep the inner anchor so screen-reader users get a real
                      link, not just a stylised row. The row-level handler is
                      a convenience affordance. */}
                  <Link
                    href={href}
                    onClick={(e) => e.stopPropagation()}
                    className="font-medium underline-offset-2 hover:underline"
                  >
                    {item.mission_title}
                  </Link>
                </td>
                <td className="px-4 py-2">
                  <DifficultyBadge difficulty={item.difficulty} />
                </td>
                <td className="px-4 py-2 text-xs text-[var(--color-muted-foreground)]">
                  {item.completed_at ? formatDateTime(item.completed_at) : "—"}
                </td>
                <td className="px-4 py-2 text-right font-mono">
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
