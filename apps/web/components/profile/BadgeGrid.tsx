import type { EarnedBadge } from "@arena/shared-types";
import { Award } from "lucide-react";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

interface BadgeGridProps {
  badges: EarnedBadge[];
  /**
   * Optional set of badge ids the viewer has actually earned. Items not in
   * the set render in a desaturated style with `aria-disabled` and a hover
   * tooltip so visitors can browse the full badge catalog without confusion.
   * When omitted, every badge is treated as earned (back-compat).
   */
  earnedIds?: Set<string>;
}

export function BadgeGrid({ badges, earnedIds }: BadgeGridProps) {
  if (badges.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--color-border)] p-6 text-center">
        <Award
          className="mx-auto size-5 text-[var(--color-muted-foreground)]"
          aria-hidden
        />
        <p className="mt-2 text-sm font-medium">No badges earned yet.</p>
        <p className="text-xs text-[var(--color-muted-foreground)]">
          Complete missions with strong supervision habits to earn badges.
        </p>
      </div>
    );
  }
  return (
    <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {badges.map((badge) => {
        const isEarned = earnedIds ? earnedIds.has(badge.id) : true;
        return (
          <li
            key={badge.id}
            data-earned={isEarned ? "true" : "false"}
            className={cn(
              "group rounded-xl border bg-[var(--color-surface)] p-4 transition-all duration-150 ease-macos",
              isEarned
                ? "border-[var(--color-border)] shadow-soft hover:-translate-y-0.5 hover:shadow-elevated"
                : "border-dashed border-[var(--color-border)] opacity-60 hover:opacity-90"
            )}
          >
            <div className="flex items-start gap-3">
              <span
                aria-hidden
                className={cn(
                  "grid size-9 place-items-center rounded-lg transition-colors duration-150",
                  isEarned
                    ? "bg-[oklch(from_var(--color-accent)_l_c_h/0.2)] text-[var(--color-accent)]"
                    : "bg-[var(--color-muted)] text-[var(--color-muted-foreground)]"
                )}
              >
                <Award className="size-4" />
              </span>
              <div>
                <p className="text-sm font-semibold">{badge.title}</p>
                <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">
                  {badge.description}
                </p>
                <p className="mt-2 text-[10px] uppercase tracking-wide text-[var(--color-muted-foreground)]">
                  {isEarned ? `Earned ${formatDate(badge.earned_at)}` : "Not yet earned"}
                </p>
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
