"use client";

import Link from "next/link";
import type { Mission, MissionLanguage } from "@arena/shared-types";
import { DifficultyBadge } from "./DifficultyBadge";
import { track } from "@/lib/telemetry";

interface MissionCardProps {
  mission: Mission;
  /**
   * 1-indexed position of this card in the catalog. Rendered as `01`, `02`, …
   * Purely visual; not the mission's persistent id.
   */
  index?: number;
  /**
   * P1-2 — when ``true``, the card carries the muted ``// recommended``
   * chip in the top-left. Only the single top-ranked recommendation for
   * the signed-in viewer gets this flag (multiple chips dilute the
   * signal). Fires ``recommendation_clicked`` on click so the catalog
   * surface can be funnel-joined with the profile / report surfaces.
   */
  recommended?: boolean;
}

/** P1-1 — short, lowercase language tag rendered on the card footer. The
 *  three values match the closed vocabulary in ``MissionLanguage``. */
const LANGUAGE_CHIP_LABEL: Record<MissionLanguage, string> = {
  typescript: "ts",
  python: "py",
  go: "go",
};

export function MissionCard({
  mission,
  index,
  recommended = false,
}: MissionCardProps) {
  const href = `/missions/${mission.id}` as const;
  const indexLabel =
    typeof index === "number" ? String(index).padStart(2, "0") : null;
  const skillSummary = (mission.skills_tested ?? []).slice(0, 2).join(" · ");
  const languageLabel = LANGUAGE_CHIP_LABEL[mission.language] ?? "ts";
  return (
    <Link
      href={href}
      onClick={() => {
        track("mission_viewed", {
          mission_id: mission.id,
          category: mission.category,
          difficulty: mission.difficulty,
          source: "catalog_card",
        });
        // P1-2 — fire the click-through telemetry whenever the user
        // engages with a card that is currently the top recommendation.
        // The catalog chip always carries ``position: 0`` because there
        // is at most one recommended card on this surface.
        if (recommended) {
          track("recommendation_clicked", {
            position: 0,
            mission_id: mission.id,
            kind: "catalog",
          });
        }
      }}
      data-recommended={recommended || undefined}
      className="group grid grid-rows-[auto_auto_1fr_auto] rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] transition-colors duration-150 ease-macos hover:bg-[var(--color-surface-elevated)] focus-visible:bg-[var(--color-surface-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
    >
      {recommended ? (
        <div
          data-testid="mission-card-recommended-chip"
          className="px-4 pt-3 font-mono text-[10.5px] uppercase tracking-[0.14em] text-[var(--color-primary)]"
        >
          {"// recommended"}
        </div>
      ) : (
        <div aria-hidden className="h-0" />
      )}
      <div className="flex items-center justify-between px-4 pt-3">
        <span className="font-mono text-[11px] text-[var(--color-muted-foreground)]">
          {indexLabel ? (
            <>
              {indexLabel}{" "}
              <span className="ml-1 border-l border-[var(--color-border-strong)] pl-2 text-[var(--color-foreground)]">
                {mission.category}
              </span>
            </>
          ) : (
            <span className="text-[var(--color-foreground)]">
              {mission.category}
            </span>
          )}
        </span>
        <DifficultyBadge difficulty={mission.difficulty} />
      </div>

      <div className="px-4 pt-3 pb-4">
        <p className="text-[15px] font-semibold leading-snug tracking-tight">
          {mission.title}
        </p>
        <p className="mt-1.5 line-clamp-2 text-[13px] leading-normal text-[var(--color-muted-foreground)]">
          {mission.short_description}
        </p>
        <p className="mt-3 truncate font-mono text-[11px] text-[var(--color-muted-foreground)]">
          failure_mode ·{" "}
          <b className="font-medium text-[var(--color-warning)]">
            {mission.failure_mode_id}
          </b>
        </p>
      </div>

      <div className="flex items-center justify-between border-t border-[var(--color-border)] px-4 py-2.5 font-mono text-[11px] text-[var(--color-muted-foreground)]">
        <span>
          ~{mission.estimated_minutes}m
          {skillSummary ? <> · {skillSummary}</> : null}
        </span>
        <span className="flex items-center gap-2">
          {/* P1-1 — language chip rendered as a code-comment-style hint so it
              matches the dojo aesthetic (``// go``). The chip is purely
              informational; the catalog filter is the interactive surface
              for narrowing by language. */}
          <span
            data-testid="mission-card-language"
            aria-label={`Language: ${mission.language}`}
            className="text-[var(--color-muted-foreground)]"
          >
            {`// ${languageLabel}`}
          </span>
          <span
            aria-hidden
            className="transition-[transform,color] duration-150 group-hover:translate-x-0.5 group-hover:text-[var(--color-foreground)]"
          >
            →
          </span>
        </span>
      </div>
    </Link>
  );
}
