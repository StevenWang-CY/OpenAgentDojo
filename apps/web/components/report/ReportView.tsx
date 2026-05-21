"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  Award,
  CheckCircle2,
  Copy,
  Loader2,
  Share2,
  Trophy,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { ApiError, getReport, getTimeline, shareReport } from "@/lib/api";
import type { ScoreReport } from "@arena/shared-types";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { track } from "@/lib/telemetry";
import { ScoreRadar } from "@/components/report/ScoreRadar";
import { DimensionBreakdown } from "@/components/report/DimensionBreakdown";
import { IdealSolution } from "@/components/report/IdealSolution";
import { TimelineReplay } from "@/components/report/TimelineReplay";

interface ReportViewProps {
  submissionId: string;
  /**
   * Optional share token forwarded by the server wrapper after reading
   * `searchParams.share`. We re-read it on the client too (for client-side
   * navigation) so a direct link with `?share=…` still authorises the fetch.
   */
  share?: string | null;
}

/**
 * Client-side report renderer. Owns:
 *   - React Query fetch of `getReport` with full loading / 404 / error / retry states.
 *   - The "share" CTA that mints a public link via `POST /reports/{id}/share`.
 *   - Conditional timeline replay (also tries `getTimeline` against the session id).
 *
 * The server wrapper handles `generateMetadata` so the OG card stays SSR.
 */
export function ReportView({ submissionId, share = null }: ReportViewProps) {
  const searchParams = useSearchParams();
  const effectiveShare = share ?? searchParams?.get("share") ?? null;

  const reportQuery = useQuery({
    queryKey: ["report", submissionId, effectiveShare ?? ""],
    queryFn: ({ signal }) => getReport(submissionId, effectiveShare, signal),
    retry: (failureCount, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return failureCount < 1;
    },
  });

  // Timeline replay is optional: backend may or may not have backfilled.
  // We key by session_id once we have the submission.
  const sessionId = reportQuery.data?.session_id;
  const timelineQuery = useQuery({
    queryKey: ["timeline", sessionId],
    queryFn: ({ signal }) => getTimeline(sessionId as string, signal),
    enabled: !!sessionId,
    retry: false,
  });

  // Fire `report_viewed` exactly once per submission id, once the payload
  // is in hand. We don't want to fire on every refetch. `passed` is only
  // meaningful when the rubric actually graded — for half-graded
  // submissions (the F4 null-guard branch below) `missed` is `undefined`
  // and we report `passed: null` rather than misleadingly emitting `true`.
  const reportedRef = React.useRef<string | null>(null);
  const total = reportQuery.data?.total_score;
  const missed = reportQuery.data?.score_report?.missed_failure_mode;
  React.useEffect(() => {
    if (reportQuery.data && reportedRef.current !== submissionId) {
      reportedRef.current = submissionId;
      track("report_viewed", {
        submission_id: submissionId,
        total_score: total,
        passed: missed === undefined ? null : missed === false,
      });
    }
  }, [reportQuery.data, submissionId, total, missed]);

  if (reportQuery.isLoading) {
    return <ReportSkeleton />;
  }

  if (reportQuery.error) {
    const err =
      reportQuery.error instanceof ApiError ? reportQuery.error : null;
    if (err?.status === 404) {
      return <NotFoundState submissionId={submissionId} />;
    }
    return (
      <ErrorState
        message={
          err?.status === 0
            ? "Couldn't reach the API. Is the backend running?"
            : (err?.message ?? "Unexpected error.")
        }
        onRetry={() => void reportQuery.refetch()}
      />
    );
  }

  const submission = reportQuery.data;
  if (!submission) {
    return <NotFoundState submissionId={submissionId} />;
  }

  // Defensive: the backend may return a `Submission` with a null
  // `score_report` if grading aborted partway through (e.g. validators
  // crashed). The TS type asserts presence — but JSON over the wire can
  // still be `null` for half-graded rows, so we narrow at runtime.
  const report = submission.score_report as ScoreReport | null | undefined;
  if (!report) {
    return (
      <ReportErrorState reason="Submission failed before grading completed." />
    );
  }
  // `missed_failure_mode` may legitimately be undefined on partially graded
  // submissions; treat that as "did not pass" since we can't claim success.
  const passed = report.missed_failure_mode === false;
  const events = timelineQuery.data ?? [];

  return (
    <main
      className="mx-auto max-w-4xl space-y-8 px-6 py-12"
      aria-labelledby="report-heading"
    >
      <ReportHeader
        submissionId={submissionId}
        totalScore={submission.total_score}
        passed={passed}
      />

      {/* Radar */}
      <section
        aria-labelledby="radar-heading"
        className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-soft"
      >
        <h2
          id="radar-heading"
          className="text-sm font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]"
        >
          Performance overview
        </h2>
        <ScoreRadar dimensions={report.dimensions} className="mt-4" />
      </section>

      {/* Dimension cards */}
      <section
        aria-labelledby="breakdown-heading"
        className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-soft"
      >
        <h2
          id="breakdown-heading"
          className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]"
        >
          Dimension breakdown
        </h2>
        <DimensionBreakdown dimensions={report.dimensions} />
      </section>

      {report.strengths.length > 0 ? (
        <BulletSection
          id="strengths-heading"
          title="Strengths"
          items={report.strengths}
          icon={
            <CheckCircle2
              className="mt-0.5 size-4 shrink-0 text-[var(--color-success)]"
              aria-hidden
            />
          }
        />
      ) : null}

      {report.weaknesses.length > 0 ? (
        <BulletSection
          id="weaknesses-heading"
          title="Areas to improve"
          items={report.weaknesses}
          icon={
            <XCircle
              className="mt-0.5 size-4 shrink-0 text-[var(--color-danger)]"
              aria-hidden
            />
          }
        />
      ) : null}

      {/* Badges */}
      {report.badges_earned.length > 0 ? (
        <section
          aria-labelledby="badges-heading"
          className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-soft"
        >
          <h2
            id="badges-heading"
            className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]"
          >
            Badges earned
          </h2>
          <ul className="flex flex-wrap gap-2" data-testid="badges-strip">
            {report.badges_earned.map((badge) => (
              <li key={badge}>
                <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-[oklch(from_var(--color-accent)_l_c_h/0.15)] px-3 py-1 text-xs font-medium text-[var(--color-foreground)]">
                  <Award
                    className="size-3.5 text-[var(--color-accent)]"
                    aria-hidden
                  />
                  {badge}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* Timeline replay */}
      {events.length > 0 ? (
        <section
          aria-labelledby="timeline-heading"
          className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-soft"
        >
          <h2
            id="timeline-heading"
            className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]"
          >
            Supervision timeline
          </h2>
          <TimelineReplay events={events} />
        </section>
      ) : null}

      {/* Ideal solution */}
      {submission.ideal_solution ? (
        <IdealSolution markdown={submission.ideal_solution} />
      ) : null}

      <footer className="flex justify-center pb-8">
        <Button asChild variant="secondary">
          <Link href="/missions">Back to missions</Link>
        </Button>
      </footer>
    </main>
  );
}

function ReportHeader({
  submissionId,
  totalScore,
  passed,
}: {
  submissionId: string;
  totalScore: number;
  passed: boolean;
}) {
  const [sharing, setSharing] = React.useState(false);
  const [sharedExpiresAt, setSharedExpiresAt] = React.useState<string | null>(
    null
  );

  async function handleShare() {
    if (sharing) return;
    track("report_shared", { submission_id: submissionId });
    setSharing(true);
    try {
      const result = await shareReport(submissionId);
      setSharedExpiresAt(result.expires_at);
      try {
        await navigator.clipboard.writeText(result.share_url);
        toast.success("Share link copied to clipboard.");
      } catch {
        // Clipboard rejected (Safari without user gesture, etc.) — still tell
        // the user the URL so they can copy it manually.
        toast.message(`Share URL: ${result.share_url}`);
      }
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Failed to mint share link."
      );
    } finally {
      setSharing(false);
    }
  }

  return (
    <header
      className="flex flex-col items-center gap-4 text-center"
      data-testid="report-header"
    >
      <Trophy className="size-8 text-[var(--color-primary)]" aria-hidden />
      <h1
        id="report-heading"
        className="text-3xl font-bold tracking-tight"
      >
        Your Score:{" "}
        <span className="text-[var(--color-primary)]">{totalScore}</span>
        <span className="text-[var(--color-muted-foreground)]">/100</span>
      </h1>
      <div className="flex items-center gap-2">
        {passed ? (
          <>
            <CheckCircle2
              className="size-4 text-[var(--color-success)]"
              aria-hidden
            />
            <span className="text-sm font-medium text-[var(--color-success)]">
              Failure mode identified
            </span>
          </>
        ) : (
          <>
            <XCircle
              className="size-4 text-[var(--color-danger)]"
              aria-hidden
            />
            <span className="text-sm font-medium text-[var(--color-danger)]">
              Failure mode missed
            </span>
          </>
        )}
      </div>
      <div className="flex flex-col items-center gap-1">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => void handleShare()}
          disabled={sharing}
        >
          {sharing ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : (
            <Share2 className="size-3.5" aria-hidden />
          )}
          {sharing ? "Generating link…" : "Share report"}
          {!sharing ? <Copy className="size-3 opacity-60" aria-hidden /> : null}
        </Button>
        {sharedExpiresAt ? (
          <p className="text-[11px] text-[var(--color-muted-foreground)]">
            Link expires {formatShareExpiry(sharedExpiresAt)}
          </p>
        ) : (
          <p className="text-[11px] text-[var(--color-muted-foreground)]">
            Share links last 30 days
          </p>
        )}
      </div>
    </header>
  );
}

/**
 * Format the share-token expiry as a friendly "in 30 days" / "on Jun 20"
 * label. We bias toward a relative form for near-term ranges so users see at
 * a glance how long they have before re-minting.
 */
function formatShareExpiry(iso: string): string {
  const target = new Date(iso);
  if (Number.isNaN(target.getTime())) return "in ~30 days";
  const deltaMs = target.getTime() - Date.now();
  const days = Math.round(deltaMs / (24 * 60 * 60 * 1000));
  if (days <= 0) return "soon";
  if (days <= 14) return `in ${days} day${days === 1 ? "" : "s"}`;
  try {
    return `on ${target.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    })}`;
  } catch {
    return `in ${days} days`;
  }
}

function BulletSection({
  id,
  title,
  items,
  icon,
}: {
  id: string;
  title: string;
  items: string[];
  icon: React.ReactNode;
}) {
  return (
    <section
      aria-labelledby={id}
      className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-soft"
    >
      <h2
        id={id}
        className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]"
      >
        {title}
      </h2>
      <ul className="space-y-1.5">
        {items.map((s, i) => (
          <li key={i} className="flex items-start gap-2 text-sm">
            {icon}
            {s}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ReportSkeleton() {
  return (
    <main className="mx-auto max-w-4xl space-y-8 px-6 py-12">
      <div className="flex flex-col items-center gap-3">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-4 w-40" />
      </div>
      <Skeleton className="h-80 rounded-xl" />
      <Skeleton className="h-64 rounded-xl" />
      <Skeleton className="h-40 rounded-xl" />
    </main>
  );
}

function NotFoundState({ submissionId }: { submissionId: string }) {
  return (
    <div className="mx-auto max-w-2xl px-6 py-16 text-center">
      <AlertCircle
        className="mx-auto size-6 text-[var(--color-muted-foreground)]"
        aria-hidden
      />
      <h1 className="mt-3 text-2xl font-semibold tracking-tight">
        Report not found
      </h1>
      <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
        No graded submission matches{" "}
        <code className="font-mono">{submissionId}</code>. It may still be
        grading or the ID is incorrect.
      </p>
      <Button asChild variant="secondary" className="mt-6">
        <Link href="/missions">Back to missions</Link>
      </Button>
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="mx-auto max-w-2xl px-6 py-16 text-center">
      <AlertCircle
        className="mx-auto size-6 text-[var(--color-danger)]"
        aria-hidden
      />
      <h1 className="mt-3 text-2xl font-semibold tracking-tight">
        Report unavailable
      </h1>
      <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
        {message}
      </p>
      <Button variant="secondary" className="mt-6" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}

/**
 * Shown when a `Submission` row exists but `score_report` is missing — i.e.
 * the grading pipeline aborted before the rubric was emitted. The state is
 * terminal (no retry CTA) because the submission can't be re-graded; the
 * user's path forward is to start a fresh mission attempt.
 */
function ReportErrorState({ reason }: { reason: string }) {
  return (
    <main
      className="mx-auto max-w-2xl px-6 py-16"
      aria-labelledby="report-error-heading"
    >
      <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-8 text-center shadow-soft">
        <AlertCircle
          className="mx-auto size-6 text-[var(--color-muted-foreground)]"
          aria-hidden
        />
        <h1
          id="report-error-heading"
          className="mt-3 text-2xl font-semibold tracking-tight"
        >
          Report not available
        </h1>
        <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
          {reason}
        </p>
        <Button asChild variant="secondary" className="mt-6">
          <Link href="/missions">Back to missions</Link>
        </Button>
      </div>
    </main>
  );
}
