"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, Inbox } from "lucide-react";
import { listMissions, ApiError } from "@/lib/api";
import type { Mission, MissionCategory } from "@arena/shared-types";
import { MissionCard } from "./MissionCard";
import { CategoryChips } from "./CategoryChips";
import { Skeleton } from "@/components/ui/Skeleton";
import { Button } from "@/components/ui/Button";

const SKELETON_CATEGORY_COUNT = 5;
const SKELETON_CARD_COUNT = 9;

export function MissionGrid() {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["missions"],
    queryFn: ({ signal }) => listMissions(signal),
  });

  const [activeCategory, setActiveCategory] = React.useState<
    MissionCategory | "all"
  >("all");

  const available = React.useMemo<MissionCategory[]>(() => {
    if (!data) return [];
    const set = new Set<MissionCategory>(data.map((m) => m.category));
    return Array.from(set).sort();
  }, [data]);

  const filtered = React.useMemo<Mission[]>(() => {
    if (!data) return [];
    if (activeCategory === "all") return data;
    return data.filter((m) => m.category === activeCategory);
  }, [data, activeCategory]);

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

  if (!data || data.length === 0) {
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
    <div>
      {available.length > 0 ? (
        <CategoryChips
          available={available}
          active={activeCategory}
          onChange={setActiveCategory}
        />
      ) : null}
      <div className="mt-8 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {filtered.map((mission, i) => (
          <MissionCard key={mission.id} mission={mission} index={i + 1} />
        ))}
      </div>
      {filtered.length === 0 ? (
        <p className="mt-4 font-mono text-xs text-[var(--color-muted-foreground)]">
          {"// no missions match this filter yet."}
        </p>
      ) : null}
    </div>
  );
}
