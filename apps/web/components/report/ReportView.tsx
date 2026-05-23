"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, Copy, Loader2, Share2 } from "lucide-react";
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

const REQUIRED_DIMENSIONS = [
  "final_correctness",
  "verification",
  "agent_review",
  "prompt_quality",
  "context_selection",
  "safety",
  "diff_minimality",
] as const;

function hasAllDimensions(
  dimensions: ScoreReport["dimensions"] | undefined | null,
): dimensions is ScoreReport["dimensions"] {
  if (!dimensions || typeof dimensions !== "object") return false;
  for (const key of REQUIRED_DIMENSIONS) {
    const dim = (dimensions as Record<string, unknown>)[key];
    if (!dim || typeof dim !== "object") return false;
  }
  return true;
}

interface ReportViewProps {
  submissionId: string;
  share?: string | null;
}

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

  const sessionId = reportQuery.data?.session_id;
  const timelineQuery = useQuery({
    queryKey: ["timeline", sessionId],
    queryFn: ({ signal }) => getTimeline(sessionId as string, signal),
    enabled: !!sessionId,
    retry: false,
  });

  const reportedRef = React.useRef<string | null>(null);
  const total = reportQuery.data?.total_score;
  const missed = reportQuery.data?.score_report?.missed_failure_mode;
  // Telemetry fires exactly once per submission per mount via `reportedRef`.
  // We intentionally do NOT include `effectiveShare` in the deps array — it
  // doesn't change the identity of the report being viewed, and adding it
  // would cause a duplicate event if a viewer switched between owned-view
  // and share-token-view of the same submission. The dedupe key is the
  // submission id, not the URL.
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
  if (!submission) return <NotFoundState submissionId={submissionId} />;
  const report = submission.score_report as ScoreReport | null | undefined;
  if (!report) {
    return (
      <ReportErrorState reason="Submission failed before grading completed." />
    );
  }
  if (!hasAllDimensions(report.dimensions)) {
    return <ReportErrorState reason="Score report is incomplete." />;
  }

  const passed = report.missed_failure_mode === false;
  const events = timelineQuery.data ?? [];

  return (
    <main
      className="mx-auto max-w-4xl px-6 py-10"
      aria-labelledby="report-heading"
    >
      <p className="inline-flex items-center gap-2 font-mono text-xs text-[var(--color-muted-foreground)]">
        <Link
          href="/missions"
          className="transition-colors hover:text-[var(--color-foreground)]"
        >
          ← missions
        </Link>
        <span aria-hidden className="opacity-50">/</span>
        <span className="text-[var(--color-foreground)]">
          report · {submissionId.slice(0, 12)}
          {submissionId.length > 12 ? "…" : ""}
        </span>
      </p>

      <ReportHeader
        submissionId={submissionId}
        totalScore={submission.total_score}
        effectiveMax={report.effective_max ?? 100}
        passed={passed}
        hiddenTestsPassed={countHiddenTestsPassed(report)}
        hiddenTestsTotal={countHiddenTestsTotal(report)}
      />

      {report.feedback_narrative && report.feedback_narrative.length > 0 ? (
        <Section title="what to work on next" id="diagnostics-heading">
          <DiagnosticList diagnostics={report.feedback_narrative} />
        </Section>
      ) : null}

      <Section title="performance overview" id="performance-heading">
        <div className="grid gap-8 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)] lg:items-center">
          <div className="-mx-2 lg:mx-0">
            <ScoreRadar
              dimensions={report.dimensions}
              aria-labelledby="performance-heading"
            />
          </div>
          <DimensionBreakdown dimensions={report.dimensions} />
        </div>
      </Section>

      {report.strengths.length > 0 ? (
        <Section title="strengths">
          <BulletList items={report.strengths} kind="ok" />
        </Section>
      ) : null}

      {report.weaknesses.length > 0 ? (
        <Section title="areas to improve">
          <BulletList items={report.weaknesses} kind="bad" />
        </Section>
      ) : null}

      {report.badges_earned.length > 0 ? (
        <Section title="badges earned">
          <ul className="flex flex-wrap gap-1.5" data-testid="badges-strip">
            {report.badges_earned.map((badge) => (
              <li key={badge}>
                <span className="inline-flex items-center gap-1.5 rounded border border-[var(--color-border-strong)] px-2.5 py-1 font-mono text-[11px]">
                  <b className="font-medium text-[var(--color-primary)]">+</b>
                  {badge}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {events.length > 0 ? (
        <Section title="supervision timeline">
          <TimelineReplay events={events} />
        </Section>
      ) : null}

      {submission.ideal_solution ? (
        <Section title="ideal solution">
          <IdealSolution markdown={submission.ideal_solution} />
        </Section>
      ) : null}

      <footer className="mt-10 flex flex-wrap items-center justify-between gap-3">
        <Button asChild variant="secondary">
          <Link href="/missions">← Back to missions</Link>
        </Button>
        <Button asChild>
          <Link href={nextMissionHref(report)}>Next mission →</Link>
        </Button>
      </footer>
    </main>
  );
}

function ReportHeader({
  submissionId,
  totalScore,
  effectiveMax,
  passed,
  hiddenTestsPassed,
  hiddenTestsTotal,
}: {
  submissionId: string;
  totalScore: number;
  effectiveMax: number;
  passed: boolean;
  hiddenTestsPassed: number | null;
  hiddenTestsTotal: number | null;
}) {
  const [sharing, setSharing] = React.useState(false);
  const [sharedExpiresAt, setSharedExpiresAt] = React.useState<string | null>(
    null,
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
        toast.message(`Share URL: ${result.share_url}`);
      }
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Failed to mint share link.",
      );
    } finally {
      setSharing(false);
    }
  }

  return (
    <header
      className="mt-3 grid grid-cols-1 items-center gap-6 border-b border-[var(--color-border)] py-7 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:gap-8"
      data-testid="report-header"
    >
      <h1
        id="report-heading"
        aria-label={`Score ${totalScore} out of ${effectiveMax}`}
        className="font-mono text-[80px] font-semibold leading-none tracking-[-0.04em] tabular-nums"
      >
        {totalScore}
        <span className="ml-1 text-[26px] font-medium text-[var(--color-muted-foreground)]">
          {" "}
          / {effectiveMax}
        </span>
      </h1>

      <div className="min-w-0 font-mono text-xs text-[var(--color-muted-foreground)]">
        <p className="uppercase tracking-[0.08em]">
          submission ·{" "}
          <span className="text-[var(--color-foreground)]">{submissionId}</span>
        </p>
        <p
          className={
            "mt-1.5 inline-flex items-center gap-2 text-sm font-medium " +
            (passed
              ? "text-[var(--color-success)]"
              : "text-[var(--color-danger)]")
          }
        >
          <span aria-hidden className="font-mono font-semibold">
            {passed ? "✓" : "✕"}
          </span>
          {passed ? "Failure mode identified" : "Failure mode missed"}
        </p>
        {hiddenTestsPassed != null && hiddenTestsTotal != null ? (
          <p className="mt-1.5">
            {hiddenTestsPassed} / {hiddenTestsTotal} hidden tests passing
          </p>
        ) : null}
      </div>

      <div className="flex flex-col items-end gap-1.5">
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
        <p className="font-mono text-[10.5px] text-[var(--color-muted-foreground)]">
          {sharedExpiresAt
            ? `link expires ${formatShareExpiry(sharedExpiresAt)}`
            : "share links last 30 days"}
        </p>
      </div>
    </header>
  );
}

function Section({
  title,
  id,
  children,
}: {
  title: string;
  id?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      aria-labelledby={id}
      className="border-b border-[var(--color-border)] py-7 last:border-b-0"
    >
      <h2
        id={id}
        className="mb-4 font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]"
      >
        {"// "}
        {title}
      </h2>
      {children}
    </section>
  );
}

interface DiagnosticEntry {
  dimension: string;
  score: number | null;
  max: number;
  cause: string;
  recommendation: string;
  recommended_mission_ids: string[];
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

function DiagnosticList({ diagnostics }: { diagnostics: DiagnosticEntry[] }) {
  return (
    <ol className="grid gap-4" data-testid="feedback-narrative">
      {diagnostics.map((d, i) => {
        const label = DIMENSION_LABELS[d.dimension] ?? d.dimension;
        return (
          <li
            key={`${d.dimension}-${i}`}
            className="border-l-2 border-[var(--color-border-strong)] pl-4"
          >
            <div className="flex items-baseline justify-between gap-3">
              <h3 className="text-sm font-semibold">{label}</h3>
              <span className="font-mono text-xs text-[var(--color-muted-foreground)] tabular-nums">
                {d.score == null ? "—" : d.score} / {d.max}
              </span>
            </div>
            <p className="mt-2 text-sm leading-relaxed">{d.cause}</p>
            <p className="mt-2 text-sm leading-relaxed text-[var(--color-muted-foreground)]">
              {d.recommendation}
            </p>
            {d.recommended_mission_ids.length > 0 ? (
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {d.recommended_mission_ids.map((mid) => (
                  <li key={mid}>
                    <Link
                      href={`/missions/${mid}`}
                      className="inline-flex items-center gap-1 rounded border border-[var(--color-border-strong)] px-2 py-1 font-mono text-[11px] transition-colors hover:border-[var(--color-foreground)] hover:text-[var(--color-foreground)]"
                    >
                      → {mid}
                    </Link>
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

function BulletList({
  items,
  kind,
}: {
  items: string[];
  kind: "ok" | "bad";
}) {
  const glyph = kind === "ok" ? "✓" : "✕";
  const klass =
    kind === "ok"
      ? "text-[var(--color-success)]"
      : "text-[var(--color-danger)]";
  return (
    <ul className="grid gap-1.5 text-sm">
      {items.map((s, i) => (
        <li
          key={i}
          className="grid grid-cols-[18px_minmax(0,1fr)] items-start gap-2"
        >
          <span aria-hidden className={`font-mono font-semibold ${klass}`}>
            {glyph}
          </span>
          <span>{s}</span>
        </li>
      ))}
    </ul>
  );
}

function ReportSkeleton() {
  return (
    <main className="mx-auto max-w-4xl space-y-6 px-6 py-10">
      <Skeleton className="h-4 w-40" />
      <Skeleton className="h-28 rounded-lg" />
      <Skeleton className="h-72 rounded-lg" />
      <Skeleton className="h-40 rounded-lg" />
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

function ReportErrorState({ reason }: { reason: string }) {
  return (
    <main
      className="mx-auto max-w-2xl px-6 py-16"
      aria-labelledby="report-error-heading"
    >
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-8 text-center">
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

/** Prefer the first recommended mission from the feedback narrative when
 *  present, otherwise fall back to the catalog so the user can pick. */
function nextMissionHref(report: ScoreReport): string {
  const recs = report.feedback_narrative ?? [];
  for (const entry of recs) {
    const first = entry.recommended_mission_ids?.[0];
    if (first) return `/missions/${first}`;
  }
  return "/missions";
}

// Match both partial-pass ("X/Y hidden tests passed") and all-pass
// ("+12: all hidden tests pass (N/N)") signals emitted by the grader.
const HIDDEN_TEST_SIGNAL = /hidden tests? (?:passed|pass)/i;

function countHiddenTestsPassed(report: ScoreReport): number | null {
  const sig = report.dimensions?.final_correctness?.signals?.find((s) =>
    HIDDEN_TEST_SIGNAL.test(s),
  );
  const m = sig?.match(/(\d+)\s*\/\s*(\d+)/);
  return m ? Number(m[1]) : null;
}
function countHiddenTestsTotal(report: ScoreReport): number | null {
  const sig = report.dimensions?.final_correctness?.signals?.find((s) =>
    HIDDEN_TEST_SIGNAL.test(s),
  );
  const m = sig?.match(/(\d+)\s*\/\s*(\d+)/);
  return m ? Number(m[2]) : null;
}
