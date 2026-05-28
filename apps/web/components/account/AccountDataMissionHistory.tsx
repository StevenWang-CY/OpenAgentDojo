"use client";

/**
 * Wave 2C — per-submission replay affordance on the account Data tab.
 *
 * The account Data tab already surfaces the bulk export (one ZIP that
 * bundles every session / submission / event the user has produced) and a
 * discoverability aside pointing to the report-page share dropdown. This
 * component closes the third gap: a *per-row* Replay button so the user
 * can grab a single submission's deterministic replay artefact straight
 * from the Data tab without round-tripping through a report page.
 *
 * Data source: ``GET /api/v1/profiles/{me.handle}`` (the same payload that
 * powers the public profile). The endpoint returns the user's last 25
 * graded sessions, each carrying a ``submission_id`` (added in this
 * wave) — when present, we render the Replay button; when ``null``
 * (legacy / non-graded row that somehow slipped past the status filter),
 * we omit the affordance instead of wiring a broken click.
 *
 * UX:
 *   - The mission title links to ``/missions/{id}`` (matching
 *     ``MissionHistoryTable``'s anchor target).
 *   - The Replay button is a small inline button — same visual treatment
 *     as the ShareDropdown's "Download replay (ZIP)" entry — that calls
 *     ``downloadReplayZip(submission_id)`` and mirrors the ShareDropdown's
 *     toast.promise + telemetry envelope so dashboards roll up cleanly.
 *
 * Auth: the endpoint is public and accepts a ``viewer`` cookie. We rely
 * on the parent ``AccountView`` having already loaded ``/me`` (so
 * ``me.handle`` is non-null by the time we mount). The Replay download
 * itself is cookie-authed (owner path — no share token forwarding
 * because the user is grabbing their own submission).
 */

import * as React from "react";
import Link from "next/link";
import { Download, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useQuery } from "@tanstack/react-query";
import type { MissionHistoryItem } from "@arena/shared-types";
import { ApiError, downloadReplayZip, getProfile } from "@/lib/api";
import { track } from "@/lib/telemetry";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { DifficultyBadge } from "@/components/catalog/DifficultyBadge";
import { Skeleton } from "@/components/ui/Skeleton";
import { SectionLabel } from "./AccountView";

interface AccountDataMissionHistoryProps {
  /** The signed-in user's handle. Required — the parent only renders this
   *  component once ``/me`` has resolved, so we don't gate on null. */
  handle: string;
}

export function AccountDataMissionHistory({
  handle,
}: AccountDataMissionHistoryProps) {
  const query = useQuery({
    queryKey: ["profiles", handle, "history-for-replay"],
    queryFn: ({ signal }) => getProfile(handle, signal),
    // The bulk export poll on the same tab fires every 2s — keep this
    // query stale a bit longer so we don't refetch on every poll tick.
    staleTime: 30_000,
    retry: (failureCount, err) => {
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        return false;
      }
      return failureCount < 1;
    },
  });

  return (
    <section
      aria-labelledby="account-data-history-heading"
      className="space-y-3"
      data-testid="account-data-history"
    >
      <header>
        <SectionLabel id="account-data-history-heading">
          per-submission replay
        </SectionLabel>
        <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
          Grab the deterministic replay artefact for any of your last 25
          graded submissions — same content the report-page share dropdown
          offers.
        </p>
      </header>

      {query.isLoading ? (
        <div className="space-y-2" aria-busy>
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : query.isError ? (
        <p
          role="alert"
          className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-xs text-[var(--color-muted-foreground)]"
        >
          Couldn&rsquo;t load your mission history. Reload the page to try
          again.
        </p>
      ) : (
        <ReplayHistoryTable items={query.data?.history ?? []} />
      )}
    </section>
  );
}

function ReplayHistoryTable({ items }: { items: MissionHistoryItem[] }) {
  if (items.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-[var(--color-border)] py-6 text-center font-mono text-xs text-[var(--color-muted-foreground)]">
        {"// no graded missions yet."}
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--color-border)]">
      <div className="min-w-[560px]">
        <div
          className="grid items-center gap-4 border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-4 py-2.5 font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--color-muted-foreground)] sm:px-5"
          style={{
            gridTemplateColumns:
              "minmax(0,1.6fr) 110px minmax(0,120px) 70px 110px",
          }}
        >
          <span>mission</span>
          <span>level</span>
          <span>completed</span>
          <span className="text-right">score</span>
          <span className="text-right">replay</span>
        </div>
        {items.map((item, idx) => (
          <ReplayHistoryRow key={item.session_id} item={item} isFirst={idx === 0} />
        ))}
      </div>
    </div>
  );
}

function ReplayHistoryRow({
  item,
  isFirst,
}: {
  item: MissionHistoryItem;
  isFirst: boolean;
}) {
  return (
    <div
      className="grid items-center gap-4 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3.5 last:border-b-0 sm:px-5"
      style={{
        gridTemplateColumns:
          "minmax(0,1.6fr) 110px minmax(0,120px) 70px 110px",
      }}
      data-testid={isFirst ? "account-data-history-row-first" : undefined}
    >
      <Link
        href={`/missions/${item.mission_id}`}
        className="group min-w-0 transition-colors duration-150 ease-macos motion-reduce:transition-none hover:text-[var(--color-primary)]"
      >
        <p className="truncate text-sm font-medium leading-tight">
          {item.mission_title}
        </p>
        <p className="mt-0.5 truncate font-mono text-[11px] text-[var(--color-muted-foreground)]">
          {item.mission_id}
        </p>
      </Link>
      <div>
        <DifficultyBadge difficulty={item.difficulty} />
      </div>
      <p className="truncate font-mono text-[11px] text-[var(--color-muted-foreground)]">
        {item.completed_at ? formatDateTime(item.completed_at) : "—"}
      </p>
      <p className="text-right font-mono text-sm font-semibold tabular-nums">
        {item.score ?? "—"}
      </p>
      <div className="flex justify-end">
        {item.submission_id ? (
          <ReplayButton submissionId={item.submission_id} />
        ) : (
          <span
            className="text-right font-mono text-[10.5px] text-[var(--color-muted-foreground)]"
            title="No replay artefact available for this row."
          >
            —
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * Per-row Replay button. Mirrors the ShareDropdown's onDownloadZip envelope
 * one-for-one: requested → toast.promise → succeeded / failed telemetry.
 */
function ReplayButton({ submissionId }: { submissionId: string }) {
  const [busy, setBusy] = React.useState(false);
  // Item 20 — track the in-flight download so an unmount mid-fetch
  // (e.g. the user tabs away from the Data tab) cancels the request
  // instead of leaving the connection and toast lingering. AbortError
  // is swallowed silently in the catch chain below.
  const abortRef = React.useRef<AbortController | null>(null);

  React.useEffect(() => {
    return () => {
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
    };
  }, []);

  const onClick = React.useCallback(async () => {
    if (busy) return;
    setBusy(true);
    // Defensive: if a previous click is somehow still in flight (busy
    // should already prevent this) cancel it before kicking off a
    // fresh request.
    if (abortRef.current) {
      abortRef.current.abort();
    }
    const ac = new AbortController();
    abortRef.current = ac;
    track("replay_export_requested", {
      submission_id: submissionId,
      kind: "zip",
    });
    const work: Promise<string> = (async () => {
      const result = await downloadReplayZip(submissionId, { signal: ac.signal });
      track("replay_export_succeeded", {
        submission_id: submissionId,
        kind: "zip",
        bytes: result.bytes,
      });
      return result.filename;
    })();
    toast.promise(work, {
      loading: "Building replay ZIP…",
      success: (filename) => `Downloaded ${filename}`,
      error: (err: unknown) => replayErrorMessage(err),
    });
    try {
      await work;
    } catch (err) {
      // AbortError = the user navigated away. Swallow silently — no
      // failure telemetry, no toast (the toast.promise above will
      // surface the error label, but ``replayErrorMessage`` returns
      // a generic message for non-ApiError shapes so the toast text
      // for an abort is harmless).
      if (err instanceof DOMException && err.name === "AbortError") {
        return;
      }
      track("replay_export_failed", {
        submission_id: submissionId,
        kind: "zip",
        error_class: classifyReplayError(err),
      });
    } finally {
      if (abortRef.current === ac) {
        abortRef.current = null;
      }
      setBusy(false);
    }
  }, [busy, submissionId]);

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-label="Download replay artefact"
      data-testid="replay-row-button"
      data-submission-id={submissionId}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 text-[11px] font-medium",
        "transition-[background-color,box-shadow] duration-150 ease-macos motion-reduce:transition-none hover:bg-[var(--color-muted)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-background)]",
        "disabled:cursor-not-allowed disabled:opacity-60",
      )}
    >
      {busy ? (
        <Loader2
          className="size-3 animate-spin motion-reduce:animate-none"
          aria-hidden
        />
      ) : (
        <Download className="size-3" aria-hidden />
      )}
      Replay
    </button>
  );
}

// ── Error helpers (kept local; ShareDropdown owns the canonical copy but
// we deliberately don't import private helpers across module boundaries) ──

type ReplayErrorClass = "network_error" | "not_found" | "not_graded" | "unknown";

function classifyReplayError(err: unknown): ReplayErrorClass {
  if (err instanceof ApiError) {
    if (err.status === 0) return "network_error";
    if (err.status === 404) {
      const detail =
        err.body && typeof err.body.detail === "string"
          ? err.body.detail.toLowerCase()
          : "";
      if (detail.includes("not graded") || detail.includes("tutorial")) {
        return "not_graded";
      }
      return "not_found";
    }
  }
  return "unknown";
}

function replayErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 404) {
      return "Replay not available for this submission.";
    }
    if (err.status === 0) {
      return "Network error — check your connection and try again.";
    }
    return err.message || "Could not download replay.";
  }
  return "Could not download replay.";
}
