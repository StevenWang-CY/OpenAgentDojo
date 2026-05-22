import type { EarnedBadge } from "@arena/shared-types";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

interface BadgeGridProps {
  badges: EarnedBadge[];
  /**
   * Optional set of badge ids the viewer has actually earned. Items not in
   * the set render in a desaturated style with `aria-disabled` so visitors
   * can browse the full badge catalog without confusion. When omitted, every
   * badge is treated as earned (back-compat).
   */
  earnedIds?: Set<string>;
}

export function BadgeGrid({ badges, earnedIds }: BadgeGridProps) {
  if (badges.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-[var(--color-border)] p-6 text-center">
        <p className="font-mono text-2xl text-[var(--color-muted-foreground)]">
          +
        </p>
        <p className="mt-2 text-sm font-medium">No badges earned yet.</p>
        <p className="mt-0.5 text-xs text-[var(--color-muted-foreground)]">
          Complete missions with strong supervision habits to earn badges.
        </p>
      </div>
    );
  }
  return (
    <ul className="mt-4 grid grid-cols-1 gap-0 sm:grid-cols-2 sm:gap-x-10">
      {badges.map((badge) => {
        const isEarned = earnedIds ? earnedIds.has(badge.id) : true;
        return (
          <li
            key={badge.id}
            data-earned={isEarned ? "true" : "false"}
            className={cn(
              "grid grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-3.5 border-b border-[var(--color-border)] py-3",
              !isEarned && "opacity-50",
            )}
          >
            <span
              aria-hidden
              className={cn(
                "grid size-7 place-items-center rounded border font-mono text-[14px] font-semibold",
                isEarned
                  ? "border-[var(--color-border-strong)] text-[var(--color-primary)]"
                  : "border-dashed border-[var(--color-border)] text-[var(--color-muted-foreground)]",
              )}
            >
              +
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium leading-tight">
                {badge.title}
              </p>
              <p className="mt-0.5 truncate font-mono text-[11px] text-[var(--color-muted-foreground)]">
                badge.{badge.id}
              </p>
            </div>
            <p className="text-right font-mono text-[11px] text-[var(--color-muted-foreground)]">
              {isEarned ? formatDate(badge.earned_at) : "—"}
            </p>
          </li>
        );
      })}
    </ul>
  );
}
