"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertCircle } from "lucide-react";
import { ApiError, getMission } from "@/lib/api";
import { Skeleton } from "@/components/ui/Skeleton";
import { Button } from "@/components/ui/Button";
import { DifficultyBadge } from "./DifficultyBadge";
import { StartMissionButton } from "./StartMissionButton";

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
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-9 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-72 w-full rounded-lg" />
      </div>
    );
  }

  if (error) {
    const apiError = error instanceof ApiError ? error : null;
    if (apiError?.status === 404) {
      return (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-8 text-center">
          <h2 className="text-lg font-semibold">Mission not found</h2>
          <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
            The mission{" "}
            <code className="font-mono">{missionId}</code> doesn&rsquo;t
            exist (yet).
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
        : (apiError?.message ?? "Unexpected error loading this mission.");
    return (
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-6">
        <div className="flex items-start gap-3 text-[var(--color-danger)]">
          <AlertCircle className="mt-0.5 size-5 shrink-0" aria-hidden />
          <div>
            <p className="text-sm font-medium">
              We couldn&rsquo;t load this mission.
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
          className="mt-4"
        >
          Try again
        </Button>
      </div>
    );
  }

  if (!data) return null;

  return (
    <article>
      <p className="inline-flex items-center gap-2 font-mono text-xs text-[var(--color-muted-foreground)]">
        <Link
          href="/missions"
          className="transition-colors hover:text-[var(--color-foreground)]"
        >
          ← catalog
        </Link>
        <span aria-hidden className="opacity-50">
          /
        </span>
        <span className="text-[var(--color-foreground)]">{data.id}</span>
      </p>

      <header className="mt-3 flex flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 font-mono text-[11px] uppercase tracking-[0.06em] text-[var(--color-muted-foreground)]">
            <DifficultyBadge difficulty={data.difficulty} />
            <span aria-hidden className="opacity-50">·</span>
            <span>{data.category}</span>
            <span aria-hidden className="opacity-50">·</span>
            <span>~{data.estimated_minutes}m</span>
            {data.version ? (
              <>
                <span aria-hidden className="opacity-50">·</span>
                <span>v{data.version}</span>
              </>
            ) : null}
          </p>
          <h1 className="mt-3.5 max-w-3xl text-balance text-[34px] font-semibold leading-[1.1] tracking-[-0.025em]">
            {data.title}
          </h1>
          <p className="mt-2.5 max-w-2xl text-pretty text-[var(--color-muted-foreground)]">
            {data.short_description}
          </p>
        </div>
        <StartMissionButton missionId={data.id} />
      </header>

      <div className="mt-10 grid grid-cols-1 gap-10 lg:grid-cols-[1fr_280px]">
        <section
          aria-label="Mission brief"
          className="border-l border-[var(--color-border-strong)] pl-6"
        >
          <div className="prose prose-sm prose-neutral max-w-none text-[var(--color-foreground)] [&_h2]:mt-7 [&_h2]:font-mono [&_h2]:text-[11px] [&_h2]:font-semibold [&_h2]:uppercase [&_h2]:tracking-[0.18em] [&_h2]:text-[var(--color-muted-foreground)] [&_h2:first-child]:mt-0 [&_code]:rounded [&_code]:border [&_code]:border-[var(--color-border)] [&_code]:bg-[var(--color-muted)] [&_code]:px-[5px] [&_code]:py-[1px] [&_code]:text-[0.92em] dark:prose-invert">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {data.brief}
            </ReactMarkdown>
          </div>

          {data.failure_mode_id ? (
            <div className="mt-7 rounded-r border-l-2 border-[var(--color-warning)] bg-[oklch(from_var(--color-warning)_l_c_h/0.08)] px-4 py-2.5">
              <p className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-[var(--color-muted-foreground)]">
                failure mode
              </p>
              <p className="mt-1 font-mono text-sm font-semibold text-[var(--color-warning)]">
                {data.failure_mode_id}
              </p>
            </div>
          ) : null}
        </section>

        <aside>
          <dl className="grid gap-6">
            {(data.skills_tested?.length ?? 0) > 0 ? (
              <div>
                <dt className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-[var(--color-muted-foreground)]">
                  Skills tested
                </dt>
                <dd className="mt-2 flex flex-wrap gap-1">
                  {(data.skills_tested ?? []).map((skill) => (
                    <span
                      key={skill}
                      className="inline-block rounded border border-[var(--color-border)] px-2 py-0.5 font-mono text-[11px] text-[var(--color-foreground)]"
                    >
                      {skill}
                    </span>
                  ))}
                </dd>
              </div>
            ) : null}

            <div>
              <dt className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-[var(--color-muted-foreground)]">
                Repo pack
              </dt>
              <dd className="mt-1.5 font-mono text-sm text-[var(--color-foreground)]">
                {data.repo_pack}
              </dd>
              {data.language_runtime ? (
                <p className="mt-0.5 font-mono text-[11px] text-[var(--color-muted-foreground)]">
                  {data.language_runtime}
                </p>
              ) : null}
            </div>

            {(data.visible_tests?.length ?? 0) > 0 ? (
              <div>
                <dt className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-[var(--color-muted-foreground)]">
                  Visible tests
                </dt>
                <dd className="mt-2">
                  <ul className="grid gap-1 font-mono text-xs">
                    {(data.visible_tests ?? []).map((t) => (
                      <li
                        key={t}
                        className="grid grid-cols-[18px_minmax(0,1fr)] items-baseline"
                      >
                        <span className="font-semibold text-[var(--color-success)]">
                          ✓
                        </span>
                        <span className="truncate text-[var(--color-foreground)]">
                          {t}
                        </span>
                      </li>
                    ))}
                  </ul>
                </dd>
              </div>
            ) : null}
          </dl>
        </aside>
      </div>
    </article>
  );
}
