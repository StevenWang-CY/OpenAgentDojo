"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertCircle, ChevronLeft, Clock } from "lucide-react";
import { ApiError, getMission } from "@/lib/api";
import { Skeleton } from "@/components/ui/Skeleton";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { DifficultyBadge } from "./DifficultyBadge";
import { StartMissionButton } from "./StartMissionButton";
import { formatEstimatedMinutes } from "@/lib/format";

interface MissionDetailViewProps {
  missionId: string;
}

export function MissionDetailView({ missionId }: MissionDetailViewProps) {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["mission", missionId],
    queryFn: ({ signal }) => getMission(missionId, signal),
  });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-72 w-full rounded-xl" />
      </div>
    );
  }

  if (error) {
    const apiError = error instanceof ApiError ? error : null;
    if (apiError?.status === 404) {
      return (
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-8 text-center">
          <h2 className="text-lg font-semibold">Mission not found</h2>
          <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
            The mission <code>{missionId}</code> doesn&rsquo;t exist (yet).
          </p>
          <Button asChild className="mt-4" variant="secondary">
            <Link href="/missions">Back to catalog</Link>
          </Button>
        </div>
      );
    }
    const message =
      apiError?.status === 0
        ? "Couldn't reach the API. Is the backend running on port 8000?"
        : apiError?.message ?? "Unexpected error loading this mission.";
    return (
      <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-6">
        <div className="flex items-start gap-3 text-[var(--color-danger)]">
          <AlertCircle className="mt-0.5 size-5 shrink-0" aria-hidden />
          <div>
            <p className="text-sm font-medium">We couldn&rsquo;t load this mission.</p>
            <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
              {message}
            </p>
          </div>
        </div>
        <Button
          onClick={() => refetch()}
          disabled={isFetching}
          variant="secondary"
          className="mt-4"
        >
          Try again
        </Button>
      </div>
    );
  }

  // `data` is guaranteed defined here (loading + error branches both return
  // above), but useQuery's generic still types it as `T | undefined` — the
  // guard is what narrows it for the JSX below.
  if (!data) return null;

  return (
    <article>
      <div className="flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
        <Link
          href="/missions"
          className="inline-flex items-center gap-1 transition-colors hover:text-[var(--color-foreground)]"
        >
          <ChevronLeft className="size-3.5" aria-hidden />
          Catalog
        </Link>
        <span aria-hidden>/</span>
        <span className="font-mono text-xs">{data.id}</span>
      </div>

      <header className="mt-3 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <DifficultyBadge difficulty={data.difficulty} />
            <Badge tone="outline">{data.category}</Badge>
            <span className="inline-flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
              <Clock className="size-3.5" aria-hidden />
              {formatEstimatedMinutes(data.estimated_minutes)}
            </span>
          </div>
          <h1 className="mt-3 text-balance text-3xl font-semibold tracking-tight">
            {data.title}
          </h1>
          <p className="mt-2 max-w-2xl text-[var(--color-muted-foreground)]">
            {data.short_description}
          </p>
        </div>
        <StartMissionButton missionId={data.id} />
      </header>

      <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[1fr_280px]">
        <section
          aria-label="Mission brief"
          className="prose prose-sm prose-neutral max-w-none dark:prose-invert"
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.brief}</ReactMarkdown>
        </section>

        <aside className="space-y-4">
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 shadow-soft">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]">
              Skills tested
            </h3>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {(data.skills_tested ?? []).map((skill) => (
                <Badge key={skill} tone="primary" className="lowercase">
                  {skill}
                </Badge>
              ))}
            </div>
          </div>

          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 shadow-soft">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]">
              Repo pack
            </h3>
            <p className="mt-2 font-mono text-sm">{data.repo_pack}</p>
            <p className="text-xs text-[var(--color-muted-foreground)]">
              {data.language_runtime ?? "—"}
            </p>
          </div>

          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 shadow-soft">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]">
              Visible tests
            </h3>
            <ul className="mt-2 space-y-1 text-xs">
              {(data.visible_tests ?? []).map((t) => (
                <li key={t} className="text-[var(--color-foreground)]">
                  • {t}
                </li>
              ))}
            </ul>
          </div>
        </aside>
      </div>
    </article>
  );
}
