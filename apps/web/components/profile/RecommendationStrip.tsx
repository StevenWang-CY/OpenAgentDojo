"use client";

import * as React from "react";
import Link from "next/link";
import { Clock3 } from "lucide-react";
import type {
  MissionLanguage,
  RecommendationItem,
  RecommendationSet,
} from "@arena/shared-types";
import { PUBLIC_REPO_URL } from "@/components/catalog/ComingSoonCard";
import { track } from "@/lib/telemetry";

/** P1-2 — short, lowercase language chip. Mirrors the catalog
 *  ``MissionCard``'s ``// xx`` chip so the surface reads consistently. */
const LANGUAGE_CHIP_LABEL: Record<MissionLanguage, string> = {
  typescript: "ts",
  python: "py",
  go: "go",
};

interface RecommendationStripProps {
  data: RecommendationSet;
}

/**
 * P1-2 — adaptive "what to work on next" strip for the profile owner.
 *
 * Renders the diagnosis copy + up to three ranked recommendation cards.
 * Cold-start users (no graded submissions) get the "// orientation" chip
 * + the ladder copy via the same surface; the ranking-vs-prose split
 * lives on the backend so the FE just renders what it receives.
 *
 * The strip is owner-only: ``ProfileView`` gates the mount behind a
 * ``viewer.handle === profile.handle`` check before rendering. Anonymous
 * viewers and other-user viewers never see this component.
 */
export function RecommendationStrip({ data }: RecommendationStripProps) {
  const items = data.recommendations;
  const missionIds = React.useMemo(
    () => items.map((it) => it.mission_id),
    [items],
  );
  // FE-P4 audit fix — three engine paths converge on
  // ``weakest_dim == null``:
  //   1. ``_cold_start()`` — viewer has zero graded submissions.
  //   2. ``_all_graded()`` — viewer has shipped every available
  //      mission, so the engine falls back to the freshest entries
  //      to keep them sharp.
  //   3. transient: engine returned an empty set.
  // The previous discriminator ("weakest_dim is null → cold start")
  // collapsed cases 1 and 2 into the same chip, which is a lie —
  // someone who has shipped every mission is anything but cold. We
  // now separate them by looking at the per-mission attempt count:
  // cold-start has zero attempts across every recommendation; the
  // all-graded fallback has at least one shipped+graded mission.
  const isWeakestDimNull =
    data.weakest_dim === null || data.weakest_dim === undefined;
  const isColdStart =
    isWeakestDimNull &&
    items.length > 0 &&
    items.every(
      (it) => it.status === "shipped" && (it.your_attempts ?? 0) === 0,
    );
  const isAllGraded = isWeakestDimNull && !isColdStart && items.length > 0;
  // ``mode`` value drives test selectors + the header copy below.
  const mode: "cold-start" | "all-graded" | "normal" = isColdStart
    ? "cold-start"
    : isAllGraded
      ? "all-graded"
      : "normal";

  // Fire ``recommendation_shown`` exactly once per (mission_ids, weakest_dim)
  // tuple. FE-P4 audit fix — the dedupe key now lives in
  // ``sessionStorage`` so a navigation away and back to /profile
  // doesn't re-fire telemetry for the same set. The key shape mirrors
  // the catalog surface so a quick eyeball of session storage can
  // diff the two. Wrapped in try/catch because some private-mode
  // browsers throw on storage access.
  React.useEffect(() => {
    if (items.length === 0) return;
    const weakest = data.weakest_dim ?? null;
    const key = `oad:rec-shown:profile:${missionIds.join(",")}:${weakest ?? "null"}`;
    try {
      if (typeof window !== "undefined" && window.sessionStorage.getItem(key)) {
        return;
      }
    } catch {
      // ignore — sessionStorage may be unavailable
    }
    try {
      window.sessionStorage.setItem(key, "1");
    } catch {
      // ignore — sessionStorage may be unavailable
    }
    track("recommendation_shown", {
      kind: "profile",
      weakest_dim: weakest,
      mission_ids: missionIds,
      signed_in: true,
    });
  }, [data.weakest_dim, missionIds, items.length]);

  if (items.length === 0) {
    return null;
  }

  return (
    <section
      data-testid="recommendation-strip"
      data-mode={mode}
      aria-labelledby="recommendation-strip-heading"
      className="mt-10 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5 sm:p-6"
    >
      <header className="flex items-baseline justify-between gap-2 border-b border-[var(--color-border)] pb-3">
        <h2
          id="recommendation-strip-heading"
          className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]"
        >
          {"// "}your next step
        </h2>
        <span
          data-testid="rec-strip-mode"
          data-mode={mode}
          className="sr-only"
        >
          {mode}
        </span>
        {isColdStart ? (
          <span
            data-testid="recommendation-strip-orientation-chip"
            className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-[var(--color-primary)]"
          >
            {"// orientation"}
          </span>
        ) : null}
        {isAllGraded ? (
          <span
            data-testid="recommendation-strip-all-clear-chip"
            className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-[var(--color-primary)]"
          >
            {"// all clear"}
          </span>
        ) : null}
      </header>

      <p
        data-testid="recommendation-strip-diagnosis"
        className="mt-4 max-w-[60ch] text-[13.5px] leading-relaxed text-[var(--color-foreground)]"
      >
        {data.diagnosis}
      </p>

      {isColdStart ? (
        <p className="mt-2 font-mono text-[11px] text-[var(--color-muted-foreground)]">
          {"// "}Start the ladder.
        </p>
      ) : null}
      {isAllGraded ? (
        <p className="mt-2 font-mono text-[11px] text-[var(--color-muted-foreground)]">
          {"// "}You&rsquo;ve finished the ladder — try the freshest missions
          to keep the edge sharp.
        </p>
      ) : null}

      <ul
        data-testid="recommendation-strip-cards"
        className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
      >
        {items.map((item, index) => (
          <li key={item.mission_id}>
            <RecommendationCard
              item={item}
              position={index}
              isColdStart={isColdStart}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}

interface RecommendationCardProps {
  item: RecommendationItem;
  position: number;
  isColdStart: boolean;
}

function RecommendationCard({
  item,
  position,
  isColdStart,
}: RecommendationCardProps) {
  const language = LANGUAGE_CHIP_LABEL[item.language] ?? "ts";
  const isShipped = item.status !== "coming_soon";
  const href = `/missions/${item.mission_id}`;

  const onClick = React.useCallback(() => {
    if (!isShipped) return;
    track("recommendation_clicked", {
      position,
      mission_id: item.mission_id,
      kind: "profile",
    });
  }, [isShipped, item.mission_id, position]);

  if (!isShipped) {
    // Coming-soon recommendation slot — distinct visual with the dated
    // chip + watch-repo link. Mirrors the catalog's ComingSoonCard so
    // the affordance is recognisable across surfaces.
    return (
      <div
        data-testid="recommendation-card-coming-soon"
        data-mission-id={item.mission_id}
        className="grid grid-rows-[auto_1fr_auto] rounded-lg border border-dashed border-[var(--color-border)] bg-[var(--color-surface)]/60 opacity-80"
      >
        <div className="flex items-center justify-between px-4 pt-3 font-mono text-[11px] text-[var(--color-muted-foreground)]">
          <span className="inline-flex items-center gap-1.5">
            <Clock3 className="size-3" aria-hidden />
            {(item.target_release_date ?? "").slice(0, 10) || "soon"}
          </span>
          <span className="uppercase tracking-[0.08em]">{"// up next"}</span>
        </div>
        <div className="px-4 pt-3 pb-4">
          <p className="text-[14px] font-semibold leading-snug tracking-tight text-[var(--color-muted-foreground)]">
            {item.title}
          </p>
          <p className="mt-2 font-mono text-[11px] leading-relaxed text-[var(--color-muted-foreground)]">
            <span className="text-[var(--color-muted-foreground)]">
              {"// why "}
            </span>
            {item.why}
          </p>
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
          <span aria-label={`Language: ${item.language}`}>
            {`// ${language}`}
          </span>
        </div>
      </div>
    );
  }

  // Shipped recommendation card — clickable wrapper so the entire surface
  // navigates to ``/missions/{id}``. The chip in the footer mirrors the
  // catalog ``MissionCard`` aesthetic to keep the visual rhyme.
  return (
    <Link
      href={href}
      onClick={onClick}
      data-testid="recommendation-card"
      data-mission-id={item.mission_id}
      className="group grid h-full grid-rows-[auto_1fr_auto] rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] transition-colors duration-150 ease-macos hover:bg-[var(--color-surface-elevated)] focus-visible:bg-[var(--color-surface-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
    >
      <div className="flex items-center justify-between px-4 pt-3 font-mono text-[11px] text-[var(--color-muted-foreground)]">
        <span className="uppercase tracking-[0.08em] text-[var(--color-foreground)]">
          {item.difficulty}
        </span>
        {isColdStart ? (
          <span
            data-testid="recommendation-card-orientation"
            className="uppercase tracking-[0.08em] text-[var(--color-primary)]"
          >
            {"// orientation"}
          </span>
        ) : item.your_best_score != null ? (
          <span
            data-testid="recommendation-card-best-score"
            className="tabular-nums text-[var(--color-muted-foreground)]"
          >
            your best: <b className="font-semibold text-[var(--color-foreground)]">
              {item.your_best_score}
            </b>{" "}
            / 100
          </span>
        ) : (
          <span className="text-[var(--color-muted-foreground)]">
            {"// not yet attempted"}
          </span>
        )}
      </div>
      <div className="px-4 pt-3 pb-4">
        <p className="text-[14px] font-semibold leading-snug tracking-tight">
          {item.title}
        </p>
        <p className="mt-2 font-mono text-[11px] leading-relaxed text-[var(--color-muted-foreground)]">
          <span className="text-[var(--color-muted-foreground)]">
            {"// why "}
          </span>
          {item.why}
        </p>
      </div>
      <div className="flex items-center justify-between border-t border-[var(--color-border)] px-4 py-2.5 font-mono text-[11px] text-[var(--color-muted-foreground)]">
        <span
          className="text-[var(--color-foreground)] transition-[transform,color] duration-150 group-hover:translate-x-0.5"
        >
          → Start
        </span>
        <span
          data-testid="recommendation-card-language"
          aria-label={`Language: ${item.language}`}
          className="text-[var(--color-muted-foreground)]"
        >
          {`// ${language}`}
        </span>
      </div>
    </Link>
  );
}
