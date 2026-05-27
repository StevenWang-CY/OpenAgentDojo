"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { AlertCircle, Inbox } from "lucide-react";
import { ApiError, auth, getMyRecommendations, listMissions } from "@/lib/api";
import { track } from "@/lib/telemetry";
import type {
  Mission,
  MissionCategory,
  MissionLanguage,
} from "@arena/shared-types";
import { MissionCard } from "./MissionCard";
import { CategoryChips } from "./CategoryChips";
import { LanguageFilter } from "./LanguageFilter";
import {
  FailureModeFilter,
  type FailureModeTag,
} from "./FailureModeFilter";
import { ComingSoonCard, PUBLIC_REPO_URL } from "./ComingSoonCard";
import { OrientationBanner } from "./OrientationBanner";
import { Skeleton } from "@/components/ui/Skeleton";
import { Button } from "@/components/ui/Button";

const SKELETON_CATEGORY_COUNT = 5;
const SKELETON_CARD_COUNT = 9;

export function MissionGrid() {
  const searchParams = useSearchParams();
  const tutorialQueryFlag =
    searchParams?.get("tutorial") === "completed";

  // P1-1 — always request ``?include=upcoming`` so the coming-soon row
  // can render inline beneath the live catalog without a second
  // roundtrip. The backend tags those rows ``status: 'coming_soon'`` so
  // the FE branches cheaply.
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["missions", { includeUpcoming: true }],
    queryFn: ({ signal }) =>
      listMissions(signal, { includeUpcoming: true }),
  });
  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: ({ signal }) => auth.me(signal),
    retry: (failureCount, err) => {
      if (err instanceof ApiError && (err.status === 401 || err.status === 0)) {
        return false;
      }
      return failureCount < 1;
    },
  });
  const user = meQuery.data ?? null;
  const signedIn = user !== null;

  // P1-2 — adaptive next-mission recommendation. Only fetched for
  // signed-in viewers; the catalog chip is a personal affordance and
  // anonymous visitors see the catalog flat. We use the same query key
  // as ProfileView so a profile-then-catalog navigation reuses the
  // cached set without a fresh roundtrip.
  const recommendationsQuery = useQuery({
    queryKey: ["me-recommendations"],
    queryFn: ({ signal }) => getMyRecommendations(signal),
    enabled: signedIn,
    retry: (failureCount, err) => {
      if (err instanceof ApiError && (err.status === 401 || err.status === 0)) {
        return false;
      }
      return failureCount < 1;
    },
  });
  // The catalog chip is reserved for the SINGLE top recommendation.
  // ``status: "coming_soon"`` items are skipped because the placeholder
  // mission isn't in the shipped grid (it lives in the "// up next"
  // section, which has its own affordances).
  const topRecommendedId = React.useMemo<string | null>(() => {
    const items = recommendationsQuery.data?.recommendations ?? [];
    for (const item of items) {
      if (item.status === "shipped") return item.mission_id;
    }
    return null;
  }, [recommendationsQuery.data]);

  // Fire ``recommendation_shown`` once per (kind, mission_ids,
  // weakest_dim) tuple. FE-P4 audit fix — the previous ``useRef`` only
  // deduped within a single mount lifecycle, so a navigation away and
  // back to /missions re-fired the event for the same recommendation.
  // We now persist the dedupe key in ``sessionStorage`` so it survives
  // intra-session navigation while still resetting on a new browser
  // session. Wrapped in try/catch because sessionStorage throws in
  // some private-mode Safari builds.
  React.useEffect(() => {
    if (!signedIn) return;
    if (!topRecommendedId) return;
    const weakest = recommendationsQuery.data?.weakest_dim ?? null;
    const key = `oad:rec-shown:catalog:${topRecommendedId}:${weakest ?? "null"}`;
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
      kind: "catalog",
      weakest_dim: weakest,
      mission_ids: [topRecommendedId],
      signed_in: true,
    });
  }, [signedIn, topRecommendedId, recommendationsQuery.data?.weakest_dim]);

  const [activeCategory, setActiveCategory] = React.useState<
    MissionCategory | "all"
  >("all");
  const [activeLanguage, setActiveLanguage] = React.useState<
    MissionLanguage | "all"
  >("all");
  const [activeFailureMode, setActiveFailureMode] = React.useState<
    FailureModeTag | "all"
  >("all");

  // ── Cohorts ──────────────────────────────────────────────────────────────
  //
  // ``shippedAll`` is every live mission (tutorial excluded — that lives on
  // OrientationBanner). ``upcoming`` is every ``coming_soon`` placeholder.
  // The filter pipeline below narrows each cohort independently so the
  // catalog grid + the up-next row both honour the same filter state.

  const shippedAll = React.useMemo<Mission[]>(() => {
    if (!data) return [];
    return data.filter(
      (m) => m.status === "shipped" && m.kind !== "tutorial",
    );
  }, [data]);

  const upcoming = React.useMemo<Mission[]>(() => {
    if (!data) return [];
    return data.filter((m) => m.status === "coming_soon");
  }, [data]);

  const availableCategories = React.useMemo<MissionCategory[]>(() => {
    const set = new Set<MissionCategory>(
      shippedAll
        .map((m) => m.category)
        .filter((c) => c !== "tutorial"),
    );
    return Array.from(set).sort();
  }, [shippedAll]);

  const availableLanguages = React.useMemo<ReadonlySet<MissionLanguage>>(() => {
    const set = new Set<MissionLanguage>();
    for (const m of shippedAll) set.add(m.language);
    for (const m of upcoming) set.add(m.language);
    return set;
  }, [shippedAll, upcoming]);

  const availableFailureModes = React.useMemo<ReadonlySet<string>>(() => {
    const set = new Set<string>();
    for (const m of shippedAll) {
      // Tags are the canonical source; failure_mode_id is the legacy
      // single-value field and is kept in lockstep by the validator. We
      // index both so the dropdown matches whichever surface the user
      // last edited.
      for (const t of m.tags ?? []) set.add(t);
      if (m.failure_mode_id) set.add(m.failure_mode_id);
    }
    return set;
  }, [shippedAll]);

  function matchesFilters(m: Mission): boolean {
    if (activeLanguage !== "all" && m.language !== activeLanguage) {
      return false;
    }
    if (activeFailureMode !== "all") {
      const tags = m.tags ?? [];
      if (
        !tags.includes(activeFailureMode) &&
        m.failure_mode_id !== activeFailureMode
      ) {
        return false;
      }
    }
    return true;
  }

  const filtered = React.useMemo<Mission[]>(() => {
    const byCategory =
      activeCategory === "all"
        ? shippedAll
        : shippedAll.filter((m) => m.category === activeCategory);
    return byCategory.filter(matchesFilters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shippedAll, activeCategory, activeLanguage, activeFailureMode]);

  const filteredUpcoming = React.useMemo<Mission[]>(() => {
    // The coming-soon row honours the language filter but ignores the
    // failure-mode filter (placeholders carry no tags yet). The category
    // filter is shipped-only by design — placeholders haven't been
    // bucketed into a category band yet.
    if (activeFailureMode !== "all") return [];
    if (activeLanguage === "all") return upcoming;
    return upcoming.filter((m) => m.language === activeLanguage);
  }, [upcoming, activeLanguage, activeFailureMode]);

  // FE-P4 audit fix — surface a banner when the top recommendation
  // exists but has been filtered out by the active language / failure
  // mode / category filter. Signed-out viewers never see this (they
  // never see the chip either). Without the banner, the recommendation
  // chip silently disappears as soon as the user narrows the catalog,
  // and the only escape hatch ("clear filters") is invisible.
  //
  // Hoisted ABOVE the early returns below so the hook order stays
  // stable across the loading / error / empty branches (react-hooks
  // rules-of-hooks).
  const recommendedHiddenByFilter = React.useMemo<Mission | null>(() => {
    if (!signedIn) return null;
    if (!topRecommendedId) return null;
    // Already visible in the shipped grid → no banner.
    if (filtered.some((m) => m.id === topRecommendedId)) return null;
    // Find the mission row from the unfiltered shipped cohort. If the
    // recommendation references a mission that's not in the catalog at
    // all (stale cache after a delete), silently no-op.
    const target = shippedAll.find((m) => m.id === topRecommendedId);
    return target ?? null;
  }, [signedIn, topRecommendedId, filtered, shippedAll]);

  const clearFilters = React.useCallback(() => {
    setActiveCategory("all");
    setActiveLanguage("all");
    setActiveFailureMode("all");
  }, []);

  if (isLoading) {
    return (
      <div>
        <Skeleton className="h-10 w-[480px] max-w-full rounded-lg" />
        <div className="mt-8 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: SKELETON_CARD_COUNT }).map((_, idx) => (
            <Skeleton key={idx} className="h-44 rounded-lg" />
          ))}
        </div>
        <span className="sr-only">
          Loading {SKELETON_CATEGORY_COUNT} categories…
        </span>
      </div>
    );
  }

  if (error) {
    const message =
      error instanceof ApiError
        ? error.status === 0
          ? "Couldn't reach the API. Is the backend running on port 8000?"
          : error.message
        : "Unexpected error loading missions.";
    return (
      <div className="flex flex-col items-start gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-6">
        <div className="flex items-start gap-3 text-[var(--color-danger)]">
          <AlertCircle className="mt-0.5 size-5 shrink-0" aria-hidden />
          <div>
            <p className="text-sm font-medium">
              We couldn&rsquo;t load missions.
            </p>
            <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
              {message}
            </p>
          </div>
        </div>
        <Button
          onClick={() => refetch()}
          disabled={isFetching}
          variant="secondary"
        >
          Try again
        </Button>
      </div>
    );
  }

  if (!data || (shippedAll.length === 0 && upcoming.length === 0)) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-[var(--color-border)] p-10 text-center">
        <Inbox
          className="size-6 text-[var(--color-muted-foreground)]"
          aria-hidden
        />
        <p className="text-sm font-medium">No missions published yet.</p>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          Missions appear here once the backend seed has finished. Check back
          soon.
        </p>
      </div>
    );
  }

  return (
    <div data-testid="mission-grid">
      <OrientationBanner
        user={user}
        showCompletionToast={tutorialQueryFlag}
      />
      {availableCategories.length > 0 ? (
        <CategoryChips
          available={availableCategories}
          active={activeCategory}
          onChange={setActiveCategory}
        />
      ) : null}
      <div className="mt-4 flex flex-wrap items-center gap-2.5">
        <LanguageFilter
          active={activeLanguage}
          onChange={setActiveLanguage}
          available={availableLanguages}
        />
        <FailureModeFilter
          active={activeFailureMode}
          onChange={setActiveFailureMode}
          available={availableFailureModes}
        />
      </div>
      {recommendedHiddenByFilter ? (
        <div
          data-testid="recommendation-hidden-banner"
          data-mission-id={recommendedHiddenByFilter.id}
          className="mt-4 flex flex-wrap items-center gap-2 rounded-md border border-dashed border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 font-mono text-[11px] text-[var(--color-muted-foreground)]"
        >
          <span>
            {"// recommended for you: "}
            <span className="text-[var(--color-foreground)]">
              {recommendedHiddenByFilter.title}
            </span>
          </span>
          <span aria-hidden>·</span>
          <button
            type="button"
            onClick={clearFilters}
            data-testid="recommendation-hidden-clear-filters"
            className="font-mono text-[11px] text-[var(--color-primary)] hover:underline focus-visible:underline focus-visible:outline-none"
          >
            clear filters to see it →
          </button>
        </div>
      ) : null}
      <div
        data-testid="mission-grid-shipped"
        className="mt-8 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
      >
        {filtered.map((mission, i) => (
          <MissionCard
            key={mission.id}
            mission={mission}
            index={i + 1}
            recommended={mission.id === topRecommendedId}
          />
        ))}
      </div>
      {filtered.length === 0 ? (
        <p className="mt-4 font-mono text-xs text-[var(--color-muted-foreground)]">
          {"// no missions match this filter yet."}
        </p>
      ) : null}
      {filteredUpcoming.length > 0 ? (
        <section
          data-testid="mission-grid-upcoming"
          aria-labelledby="up-next-heading"
          className="mt-12"
        >
          <div className="flex items-center justify-between">
            <h2
              id="up-next-heading"
              className="font-mono text-xs uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]"
            >
              {"// up next"}
            </h2>
            <a
              href={PUBLIC_REPO_URL}
              target="_blank"
              rel="noreferrer noopener"
              className="font-mono text-[11px] text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
            >
              watch repo ↗
            </a>
          </div>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {filteredUpcoming.map((mission) => (
              <ComingSoonCard key={mission.id} mission={mission} />
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
