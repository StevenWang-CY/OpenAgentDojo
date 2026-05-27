"use client";

import * as React from "react";
import { Clock3 } from "lucide-react";
import type { Mission, MissionLanguage } from "@arena/shared-types";
import { PUBLIC_REPO_URL } from "@/lib/repo-url";

/** Public placeholder repo URL surfaced beside the coming-soon list. The
 *  ``NEXT_PUBLIC_REPO_URL`` env var lets a deploy override this without
 *  shipping a code change. The constant now lives in ``lib/repo-url`` so
 *  the marketing footer, catalog cards, profile strip, and roadmap page
 *  all share a single source. Re-exported here for backwards-compat with
 *  existing imports. */
export { PUBLIC_REPO_URL };

const LANGUAGE_CHIP_LABEL: Record<MissionLanguage, string> = {
  typescript: "ts",
  python: "py",
  go: "go",
};

interface ComingSoonCardProps {
  mission: Mission;
}

function formatTargetDate(iso: string | null | undefined): string {
  if (!iso) return "soon";
  // Render as YYYY-MM-DD (the wire format the backend emits) — the
  // catalog deliberately avoids locale-formatting so the chip reads
  // identically in every region.
  return iso.slice(0, 10);
}

/** P1-1 — muted, informational placeholder card.
 *
 *  Visually distinct from the live ``MissionCard``: lower opacity, dashed
 *  border, no Start CTA. The header carries the dated chip with a clock
 *  glyph so the "this is coming, not shipped" signal lands instantly. */
export function ComingSoonCard({ mission }: ComingSoonCardProps) {
  const language = LANGUAGE_CHIP_LABEL[mission.language] ?? "ts";
  const dateLabel = formatTargetDate(mission.target_release_date);
  return (
    <div
      data-testid="coming-soon-card"
      data-mission-id={mission.id}
      aria-label={`Coming soon: ${mission.title}`}
      className="grid grid-rows-[auto_1fr_auto] rounded-lg border border-dashed border-[var(--color-border)] bg-[var(--color-surface)]/60 opacity-80"
    >
      <div className="flex items-center justify-between px-4 pt-3 font-mono text-[11px] text-[var(--color-muted-foreground)]">
        <span className="inline-flex items-center gap-1.5">
          <Clock3 className="size-3" aria-hidden />
          {dateLabel}
        </span>
        <span className="uppercase tracking-[0.08em]">{"// up next"}</span>
      </div>
      <div className="px-4 pt-3 pb-4">
        <p className="text-[15px] font-semibold leading-snug tracking-tight text-[var(--color-muted-foreground)]">
          {mission.title}
        </p>
        {mission.short_description ? (
          <p className="mt-1.5 line-clamp-2 text-[13px] leading-normal text-[var(--color-muted-foreground)]">
            {mission.short_description}
          </p>
        ) : null}
      </div>
      <div className="flex items-center justify-between border-t border-dashed border-[var(--color-border)] px-4 py-2.5 font-mono text-[11px] text-[var(--color-muted-foreground)]">
        <a
          href={PUBLIC_REPO_URL}
          target="_blank"
          rel="noreferrer noopener"
          className="hover:text-[var(--color-foreground)]"
        >
          watch repo ↗
        </a>
        <span aria-label={`Language: ${mission.language}`}>
          {`// ${language}`}
        </span>
      </div>
    </div>
  );
}
