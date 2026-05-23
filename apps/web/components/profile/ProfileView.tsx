"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, UserX } from "lucide-react";
import type { PublicProfile } from "@arena/shared-types";
import { ApiError, getProfile } from "@/lib/api";
import { ProfileHeader } from "./ProfileHeader";
import { ProfileRadar } from "./ProfileRadar";
import { BadgeGrid } from "./BadgeGrid";
import { MissionHistoryTable } from "./MissionHistoryTable";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { track } from "@/lib/telemetry";

interface ProfileViewProps {
  handle: string;
}

export function ProfileView({ handle }: ProfileViewProps) {
  const profileQuery = useQuery({
    queryKey: ["profile", handle],
    queryFn: ({ signal }) => getProfile(handle, signal),
    retry: (failureCount, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return failureCount < 1;
    },
  });

  const trackedHandleRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (profileQuery.data && trackedHandleRef.current !== handle) {
      trackedHandleRef.current = handle;
      track("profile_viewed", { handle });
    }
  }, [profileQuery.data, handle]);

  if (profileQuery.isLoading) {
    return (
      <main className="mx-auto max-w-5xl space-y-8 px-6 py-12">
        <Skeleton className="h-28 w-full rounded-lg" />
        <Skeleton className="h-40 w-full rounded-lg" />
        <Skeleton className="h-64 w-full rounded-lg" />
      </main>
    );
  }

  if (profileQuery.error) {
    const err =
      profileQuery.error instanceof ApiError ? profileQuery.error : null;

    if (err?.status === 404) {
      return (
        <div className="mx-auto max-w-2xl px-6 py-16 text-center">
          <UserX
            className="mx-auto size-6 text-[var(--color-muted-foreground)]"
            aria-hidden
          />
          <h1 className="mt-3 text-2xl font-semibold tracking-tight">
            Profile not found
          </h1>
          <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
            No one with the handle{" "}
            <code className="font-mono">@{handle}</code> has signed up yet.
          </p>
          <Button asChild variant="secondary" className="mt-6">
            <Link href="/missions">Browse missions</Link>
          </Button>
        </div>
      );
    }

    return (
      <div className="mx-auto max-w-2xl px-6 py-16 text-center">
        <AlertCircle
          className="mx-auto size-6 text-[var(--color-danger)]"
          aria-hidden
        />
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">
          Profile unavailable
        </h1>
        <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
          {err?.status === 0
            ? "Couldn't reach the API. Is the backend running?"
            : (err?.message ?? "Unexpected error.")}
        </p>
        <Button
          variant="secondary"
          className="mt-6"
          onClick={() => void profileQuery.refetch()}
        >
          Try again
        </Button>
      </div>
    );
  }

  const profile = profileQuery.data;
  if (!profile) return null;

  return (
    <main
      className="mx-auto max-w-5xl px-6 pt-12 pb-16"
      aria-labelledby="profile-header"
    >
      <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
        <span className="text-[var(--color-primary)]">{"//"}</span> public
        profile
      </p>

      <section className="mt-3" aria-labelledby="profile-header">
        <ProfileHeader profile={profile} />
      </section>

      {Object.keys(profile.radar_averages).length > 0 ? (
        <>
          <SectionHeading
            title="rubric averages"
            count={`across ${profile.total_missions} graded session${profile.total_missions === 1 ? "" : "s"}`}
            id="radar-heading"
          />
          <div className="mt-4">
            <ProfileRadar averages={profile.radar_averages} />
          </div>
        </>
      ) : null}

      <SectionHeading
        title="badges earned"
        count={`${profile.badges.length} earned`}
        id="badges-heading"
      />
      <BadgeGrid badges={profile.badges} />

      <div className="mt-4 flex justify-end">
        <Button asChild variant="secondary" size="sm">
          <Link href="/skills">View skill mastery →</Link>
        </Button>
      </div>

      {profile.dimension_trends &&
      Object.keys(profile.dimension_trends).length > 0 ? (
        <>
          <SectionHeading
            title="dimension trends"
            count={`${
              Object.values(profile.dimension_trends).reduce(
                (acc, arr) => acc + (arr?.length ?? 0),
                0,
              )
            } scored sessions`}
            id="trends-heading"
          />
          <DimensionTrends trends={profile.dimension_trends} />
        </>
      ) : null}

      <SectionHeading
        title="mission history"
        count={`${profile.history.length} session${profile.history.length === 1 ? "" : "s"}`}
        id="history-heading"
      />
      <MissionHistoryTable items={profile.history} />
    </main>
  );
}

const DIMENSION_LABELS: Record<string, string> = {
  final_correctness: "Final patch correctness",
  verification: "Verification discipline",
  agent_review: "Agent output review",
  prompt_quality: "Prompt quality",
  context_selection: "Context selection",
  safety: "Safety awareness",
  diff_minimality: "Diff minimality",
};

const DIMENSION_MAX: Record<string, number> = {
  final_correctness: 30,
  verification: 15,
  agent_review: 15,
  prompt_quality: 10,
  context_selection: 10,
  safety: 10,
  diff_minimality: 10,
};

const DIMENSION_ORDER = [
  "final_correctness",
  "verification",
  "agent_review",
  "prompt_quality",
  "context_selection",
  "safety",
  "diff_minimality",
];

function DimensionTrends({
  trends,
}: {
  trends: NonNullable<PublicProfile["dimension_trends"]>;
}) {
  return (
    <ul className="mt-4 grid gap-3" data-testid="dimension-trends">
      {DIMENSION_ORDER.filter((d) => (trends[d as keyof typeof trends]?.length ?? 0) > 0).map(
        (dim) => {
          const points = trends[dim as keyof typeof trends] ?? [];
          const max = DIMENSION_MAX[dim] ?? 10;
          const last5 = points.slice(-5);
          const latest = last5[last5.length - 1]?.score ?? null;
          const earliest =
            points.length > 1 ? (points[0]?.score ?? null) : null;
          const delta =
            latest != null && earliest != null && points.length > 1
              ? latest - earliest
              : null;
          return (
            <li
              key={dim}
              className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-3 border-b border-[var(--color-border)] pb-2 last:border-b-0"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">
                  {DIMENSION_LABELS[dim] ?? dim}
                </p>
                <p className="font-mono text-[10.5px] text-[var(--color-muted-foreground)] tabular-nums">
                  {points.length} session
                  {points.length === 1 ? "" : "s"}
                  {delta != null ? (
                    <>
                      {" · "}
                      <span
                        className={
                          delta > 0
                            ? "text-[var(--color-success)]"
                            : delta < 0
                              ? "text-[var(--color-danger)]"
                              : ""
                        }
                      >
                        {delta > 0 ? "+" : ""}
                        {delta} vs first
                      </span>
                    </>
                  ) : null}
                </p>
              </div>
              {last5.length > 0 ? (
                <Sparkline values={last5.map((p) => p.score)} max={max} />
              ) : (
                <span
                  aria-hidden
                  className="inline-block w-24 text-center font-mono text-xs text-[var(--color-muted-foreground)]"
                >
                  —
                </span>
              )}
              <p className="font-mono text-xs tabular-nums text-[var(--color-muted-foreground)] min-w-[3.5rem] text-right">
                <b className="font-semibold text-[var(--color-foreground)]">
                  {latest ?? "—"}
                </b>
                /{max}
              </p>
            </li>
          );
        },
      )}
    </ul>
  );
}

function Sparkline({ values, max }: { values: number[]; max: number }) {
  // Defensive guard: callers should pre-check and render an inline "—"
  // in the score cell when there are no values, so this branch is
  // unreachable in normal usage. Kept to avoid a stray SVG with no points.
  if (values.length === 0) return null;
  const w = 96;
  const h = 24;
  const pad = 2;
  const span = Math.max(values.length - 1, 1);
  const pts = values.map((v, i) => {
    const x = pad + (i * (w - pad * 2)) / span;
    const ratio = max > 0 ? Math.min(1, Math.max(0, v / max)) : 0;
    const y = h - pad - ratio * (h - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const last = values[values.length - 1] ?? 0;
  const lastRatio = max > 0 ? Math.min(1, Math.max(0, last / max)) : 0;
  const lastX = pad + ((values.length - 1) * (w - pad * 2)) / span;
  const lastY = h - pad - lastRatio * (h - pad * 2);
  return (
    <svg
      role="img"
      aria-label={`Sparkline of ${values.length} recent scores, latest ${last} of ${max}`}
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      className="text-[var(--color-foreground)]"
    >
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth="1.25"
        points={pts.join(" ")}
      />
      <circle cx={lastX} cy={lastY} r="2" fill="currentColor" />
    </svg>
  );
}

function SectionHeading({
  title,
  count,
  id,
}: {
  title: string;
  count?: string;
  id?: string;
}) {
  return (
    <div className="mt-12 flex items-baseline justify-between border-b border-[var(--color-border)] pb-2.5">
      <h2
        id={id}
        className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]"
      >
        {"// "}
        {title}
      </h2>
      {count ? (
        <p className="font-mono text-[11px] text-[var(--color-muted-foreground)]">
          {count}
        </p>
      ) : null}
    </div>
  );
}
