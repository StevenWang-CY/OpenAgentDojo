"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Flag, Loader2, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import {
  ApiError,
  auth,
  createSession,
  getMyRecommendations,
  getReport,
  getTimeline,
  shareReport,
} from "@/lib/api";
import type { ScoreReport } from "@arena/shared-types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { track } from "@/lib/telemetry";
import { ScoreRadar } from "@/components/report/ScoreRadar";
import { DimensionBreakdown } from "@/components/report/DimensionBreakdown";
import { IdealSolution } from "@/components/report/IdealSolution";
import { TimelineReplay } from "@/components/report/TimelineReplay";
import { PostMortemWalkthrough } from "@/components/report/PostMortemWalkthrough";
import { DimensionEvidence } from "@/components/report/DimensionEvidence";
import { ShareDropdown } from "@/components/report/ShareDropdown";

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

  // FE-P4 audit fix — share-token viewers must NEVER trigger a
  // ``/me/recommendations`` fetch: the call is owner-scoped and will
  // 401 for anonymous viewers, polluting telemetry and the React
  // Query cache. Gate the recommendations query behind a fresh
  // ``/auth/me`` probe and the absence of a share token.
  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: ({ signal }) => auth.me(signal),
    enabled: !effectiveShare,
    retry: (failureCount, err) => {
      if (err instanceof ApiError && (err.status === 401 || err.status === 0)) {
        return false;
      }
      return failureCount < 1;
    },
  });
  const user = meQuery.data ?? null;

  // P1-2 — live "next mission" recommendation. The legacy fallback
  // (``nextMissionHref`` reading from the score_report's
  // ``feedback_narrative[].recommended_mission_ids``) is preserved so a
  // 401 / network failure here never breaks the report footer CTA.
  // Anonymous / share-link viewers get the embedded list; owners get
  // the live engine output that reflects their newer history.
  const recommendationsQuery = useQuery({
    queryKey: ["me-recommendations"],
    queryFn: ({ signal }) => getMyRecommendations(signal),
    // FE-P4 audit fix — only owners (signed-in, not viewing via a
    // share token) hit ``/me/recommendations``. Anonymous viewers
    // and share-link viewers stay on the embedded fallback list,
    // which lives in the score_report itself.
    enabled: !effectiveShare && !!user,
    retry: (failureCount, err) => {
      if (err instanceof ApiError && (err.status === 401 || err.status === 0)) {
        return false;
      }
      return failureCount < 1;
    },
  });

  // FE-P2 audit fix — dedupe is now keyed by (submissionId, complete) so
  // a partial report that later finishes grading and re-renders as the
  // complete view still emits a second `report_viewed` event. Analytics
  // can split incomplete (early-render / failed-grade) from complete.
  const reportedRef = React.useRef<string | null>(null);
  const total = reportQuery.data?.total_score;
  const missed = reportQuery.data?.score_report?.missed_failure_mode;
  const reportComplete = hasAllDimensions(
    reportQuery.data?.score_report?.dimensions,
  );

  // P0-2 — single highlight slot consumed by the timeline replay. Hoisted
  // above the early returns so the hook order stays stable across renders.
  const [highlightEventId, setHighlightEventId] = React.useState<number | null>(
    null,
  );
  const scrollToEvent = React.useCallback((eventId: number) => {
    // Bump to null first so consecutive clicks on the same id still
    // re-fire the pulse.
    setHighlightEventId(null);
    requestAnimationFrame(() => setHighlightEventId(eventId));
  }, []);
  // Telemetry fires once per (submissionId, complete) tuple per mount via
  // `reportedRef`. We intentionally do NOT include `effectiveShare` in the
  // deps array — it doesn't change the identity of the report being viewed,
  // and adding it would cause a duplicate event if a viewer switched
  // between owned-view and share-token-view of the same submission.
  React.useEffect(() => {
    if (!reportQuery.data) return;
    const dedupeKey = `${submissionId}::${reportComplete ? "complete" : "partial"}`;
    if (reportedRef.current === dedupeKey) return;
    reportedRef.current = dedupeKey;
    track("report_viewed", {
      submission_id: submissionId,
      total_score: total,
      passed: missed === undefined ? null : missed === false,
      complete: reportComplete,
    });
  }, [reportQuery.data, submissionId, total, missed, reportComplete]);

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
        scoreCapReason={submission.score_cap_reason ?? report.score_cap_reason ?? null}
        uncappedTotal={report.uncapped_total ?? null}
      />

      {/* P0-8 — verified vs honor-mode chip pinned beside the score so a
           viewer immediately knows whether this attempt is a credential
           or practice. The chip mirrors ``submission.verified`` (stamped
           from ``session.mode == 'proctored'`` at grade time). */}
      <div className="mt-2 flex items-center gap-2">
        {submission.verified ? (
          <span
            data-testid="verified-badge"
            className="inline-flex items-center gap-1.5 rounded-full border border-[oklch(from_var(--color-primary)_l_c_h/0.5)] bg-[oklch(from_var(--color-primary)_l_c_h/0.08)] px-2.5 py-0.5 text-[11px] font-medium text-[var(--color-primary)]"
          >
            <span aria-hidden className="font-mono">{"//"}</span>
            verified · proctored
          </span>
        ) : (
          <span
            data-testid="honor-mode-chip"
            className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-0.5 text-[11px] text-[var(--color-muted-foreground)]"
          >
            <span aria-hidden className="font-mono">{"//"}</span>
            honor mode · practice score
          </span>
        )}
      </div>

      {/* P0-2 — post-mortem walkthrough leads the report: critical
          moment + three-way diff. This is the training surface, not
          the measurement readout. */}
      <Section title="what you take away" id="walkthrough-heading">
        <PostMortemWalkthrough
          submission={submission}
          events={events}
          onScrollToEvent={scrollToEvent}
        />
      </Section>

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
          <DimensionEvidence
            tone="ok"
            entries={report.strengths}
            onScrollToEvent={scrollToEvent}
          />
        </Section>
      ) : null}

      {report.weaknesses.length > 0 ? (
        <Section title="areas to improve">
          <DimensionEvidence
            tone="bad"
            entries={report.weaknesses}
            onScrollToEvent={scrollToEvent}
          />
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
          <TimelineReplay
            events={events}
            highlightEventId={highlightEventId}
          />
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
        <div className="flex flex-wrap items-center gap-2">
          {/* P0-3 — Retry this mission. Wires the new session back to the
              prior one via previous_session_id so the audit trail links
              attempts together. Falls back to a plain mission-detail link
              if the mission id is unavailable (legacy reports). */}
          {submission.mission_id ? (
            <RetryMissionButton
              missionId={submission.mission_id}
              previousSessionId={submission.session_id}
            />
          ) : null}
          <NextMissionButton
            recommendation={recommendationsQuery.data ?? null}
            fallbackReport={report}
          />
        </div>
      </footer>
    </main>
  );
}

/**
 * P1-2 — "Next mission →" CTA backed by the live recommendation engine.
 *
 * The button prefers the top item from ``/me/recommendations`` (live,
 * personalised, never stale). When the live fetch fails or returns no
 * shipped items (401 anonymous viewer, network outage, share-link
 * viewer), it falls back to the score_report's embedded
 * ``feedback_narrative[].recommended_mission_ids`` so the CTA always
 * navigates somewhere meaningful.
 *
 * Emits ``recommendation_shown`` + ``recommendation_clicked`` for the
 * live path only; the legacy fallback is a no-op for the funnel because
 * its source (the embedded score_report list) doesn't carry the same
 * deterministic ranking guarantees.
 */
function NextMissionButton({
  recommendation,
  fallbackReport,
}: {
  recommendation: import("@arena/shared-types").RecommendationSet | null;
  fallbackReport: ScoreReport;
}) {
  const liveTop = React.useMemo(() => {
    if (!recommendation) return null;
    for (const it of recommendation.recommendations) {
      if (it.status === "shipped") return it;
    }
    return null;
  }, [recommendation]);

  const href = liveTop
    ? `/missions/${liveTop.mission_id}`
    : nextMissionHref(fallbackReport);

  const shownRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!liveTop) return;
    if (shownRef.current === liveTop.mission_id) return;
    shownRef.current = liveTop.mission_id;
    track("recommendation_shown", {
      kind: "report",
      weakest_dim: recommendation?.weakest_dim ?? null,
      mission_ids: [liveTop.mission_id],
      signed_in: true,
    });
  }, [liveTop, recommendation?.weakest_dim]);

  const onClick = React.useCallback(() => {
    if (!liveTop) return;
    track("recommendation_clicked", {
      position: 0,
      mission_id: liveTop.mission_id,
      kind: "report",
    });
  }, [liveTop]);

  return (
    <Button asChild>
      <Link href={href} onClick={onClick} data-testid="report-next-mission">
        Next mission →
      </Link>
    </Button>
  );
}

interface RetryMissionButtonProps {
  missionId: string;
  previousSessionId: string;
}

/**
 * Per-mission sessionStorage key for the retry-after deadline. Persisting
 * survives the inevitable navigation-then-back pattern after a 429 — the
 * cooldown is a server-side budget, so it has to outlive a component unmount
 * or the user just walks the button back into another 429 storm.
 */
function retryAfterStorageKey(missionId: string): string {
  return `oad.retry_after.${missionId}`;
}

/** Read the persisted retry-after deadline (epoch ms) for this mission.
 *  Returns null on SSR, missing key, garbage value, or an already-elapsed
 *  deadline (with a cleanup write in that last case). */
function readRetryAfterDeadline(missionId: string): number | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(retryAfterStorageKey(missionId));
    if (!raw) return null;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return null;
    if (parsed <= Date.now()) {
      window.sessionStorage.removeItem(retryAfterStorageKey(missionId));
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function writeRetryAfterDeadline(missionId: string, deadline: number): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(
      retryAfterStorageKey(missionId),
      String(deadline),
    );
  } catch {
    /* sessionStorage may be unavailable (Safari private mode quirks) */
  }
}

function clearRetryAfterDeadline(missionId: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(retryAfterStorageKey(missionId));
  } catch {
    /* ignore */
  }
}

function RetryMissionButton({
  missionId,
  previousSessionId,
}: RetryMissionButtonProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  // FE-P1 audit fix — when the user hits 429 (rate limit), disable the
  // button for the retry-after window AND surface a live countdown in
  // the label so they don't spam the button and rack up 429s. The
  // deadline is persisted to sessionStorage keyed by missionId so a
  // navigate-away → come-back cycle can't sneak the user past the
  // cooldown.
  const [retryAfterDeadline, setRetryAfterDeadline] = React.useState<
    number | null
  >(() => readRetryAfterDeadline(missionId));
  const [, force] = React.useReducer((n: number) => n + 1, 0);
  // Re-sync from storage when the mission id changes (defensive — the
  // current callsite mounts a fresh button per submission, but the hook
  // should still behave correctly if reused).
  React.useEffect(() => {
    setRetryAfterDeadline(readRetryAfterDeadline(missionId));
  }, [missionId]);
  React.useEffect(() => {
    if (retryAfterDeadline === null) return;
    const remaining = retryAfterDeadline - Date.now();
    if (remaining <= 0) {
      setRetryAfterDeadline(null);
      clearRetryAfterDeadline(missionId);
      return;
    }
    const interval = window.setInterval(() => {
      if (Date.now() >= retryAfterDeadline) {
        setRetryAfterDeadline(null);
        clearRetryAfterDeadline(missionId);
        return;
      }
      force();
    }, 1000);
    return () => window.clearInterval(interval);
  }, [retryAfterDeadline, missionId]);

  const mutation = useMutation({
    mutationFn: () =>
      createSession({
        mission_id: missionId,
        previous_session_id: previousSessionId,
      }),
    onSuccess: (session) => {
      track("mission_retried", {
        mission_id: missionId,
        previous_session_id: previousSessionId,
      });
      // FE-P1 audit fix — invalidate the mission detail cache so the
      // YourAttemptsStrip on /missions/{id} reflects the new attempt
      // immediately when the user returns. Also invalidate the profile
      // + skills caches so the radar updates on next view.
      queryClient.invalidateQueries({ queryKey: ["mission", missionId] });
      queryClient.invalidateQueries({ queryKey: ["profile"] });
      queryClient.invalidateQueries({ queryKey: ["skills"] });
      // FE-P4 audit fix — a retry creates a new attempt that may shift
      // the engine's weakest-dim picture once it grades, so the cached
      // ``/me/recommendations`` set is stale by definition. Invalidate
      // here so the catalog chip + profile strip + report footer all
      // refresh on the next read instead of pinning to the pre-retry
      // ranking.
      queryClient.invalidateQueries({ queryKey: ["me-recommendations"] });
      // Land directly in the workspace — the shell handles the
      // provisioning state and transitions to active on its own.
      router.push(`/workspace/${session.id}`);
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        // 409 active_session_exists — bounce to the live session so the
        // user can finish it before retrying. Mirrors StartMissionButton.
        if (err.status === 409) {
          const detail = err.body?.detail;
          if (
            detail &&
            typeof detail === "object" &&
            "active_session_id" in detail
          ) {
            const sid = (detail as { active_session_id?: string })
              .active_session_id;
            if (typeof sid === "string") {
              toast.warning(
                "You already have an active session — finish or abandon it first.",
                {
                  action: {
                    label: "Resume",
                    onClick: () => router.push(`/workspace/${sid}`),
                  },
                },
              );
              return;
            }
          }
          // Fall through to the generic 409 toast — at least we surface
          // a real error instead of swallowing it silently.
          toast.error(
            err.message || "Finish your current attempt before retrying.",
          );
          return;
        }
        if (err.status === 429) {
          const wait = err.retryAfterSeconds ?? 60;
          const deadline = Date.now() + wait * 1000;
          // FE-P1 audit fix — disable the button AND show a live countdown
          // until the rate-limit budget refills, instead of letting the
          // user spam the button and accumulate more 429s. Persist the
          // deadline so a tab switch / nav-away can't reset the cooldown.
          setRetryAfterDeadline(deadline);
          writeRetryAfterDeadline(missionId, deadline);
          toast.error(
            `You're submitting too quickly. Try again in ${wait}s.`,
          );
          return;
        }
        toast.error(err.message || "Could not start a retry.");
      } else {
        toast.error("Could not start a retry.");
      }
    },
  });

  const secondsRemaining =
    retryAfterDeadline === null
      ? 0
      : Math.max(0, Math.ceil((retryAfterDeadline - Date.now()) / 1000));
  const disabled = mutation.isPending || secondsRemaining > 0;
  const label = mutation.isPending
    ? "Starting retry…"
    : secondsRemaining > 0
      ? `Try again in ${secondsRemaining}s`
      : "Retry this mission";

  return (
    <Button
      variant="secondary"
      onClick={() => mutation.mutate()}
      disabled={disabled}
      data-testid="retry-mission-button"
    >
      {mutation.isPending ? (
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
      ) : (
        <RotateCcw className="size-3.5" aria-hidden />
      )}
      {label}
    </Button>
  );
}

function ReportHeader({
  submissionId,
  totalScore,
  effectiveMax,
  passed,
  hiddenTestsPassed,
  hiddenTestsTotal,
  scoreCapReason,
  uncappedTotal,
}: {
  submissionId: string;
  totalScore: number;
  effectiveMax: number;
  passed: boolean;
  hiddenTestsPassed: number | null;
  hiddenTestsTotal: number | null;
  /** P0-4 — when set, render the cap chip beside the pass/fail row. */
  scoreCapReason: string | null;
  /** P0-4 — the uncapped (honest) total. Surfaced as "would have scored
   *  X" beside the cap chip when greater than the displayed total. */
  uncappedTotal: number | null;
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
        {scoreCapReason === "gave_up" ? (
          <Badge
            tone="warning"
            className="mt-2 normal-case tracking-normal"
            data-testid="gave-up-chip"
          >
            <Flag className="size-3" aria-hidden />
            Gave up — score capped at 50/100
            {uncappedTotal != null && uncappedTotal > totalScore ? (
              <span className="ml-1 font-mono text-[10px] text-[var(--color-muted-foreground)]">
                (uncapped {uncappedTotal})
              </span>
            ) : null}
          </Badge>
        ) : null}
      </div>

      <div className="flex flex-col items-end gap-1.5">
        <ShareDropdown
          submissionId={submissionId}
          onCopyLink={() => void handleShare()}
          sharing={sharing}
        />
        <p className="font-mono text-[11px] text-[var(--color-muted-foreground)]">
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
