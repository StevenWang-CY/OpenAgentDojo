"use client";

/**
 * P0-6 — Data export panel.
 *
 * Lets the user kick a one-off export of their account data (sessions,
 * prompts, file changes, etc.). The backend job zips the bundle, uploads to
 * R2, and the panel polls for completion.
 *
 * UX states:
 *   - idle: no export in flight, "Request export" CTA. Renders a dashed
 *     empty-state block so the surface doesn't feel orphaned.
 *   - queued / running: polling indicator, "preparing your export". Polling
 *     runs through React Query so a tab switch back to the page picks up
 *     the same in-flight export from the cache.
 *   - ready: ``Download`` button opens the signed R2 URL in a new tab + the
 *     export's ``expires_at`` is surfaced as a relative deadline.
 *   - failed: the worker's ``error`` is rendered verbatim with a "try
 *     again" CTA. Failed exports never block new requests.
 *   - expired: same affordance as failed (the daily worker flips to
 *     ``expired`` once ``expires_at`` passes), with copy explaining the
 *     signed URL has aged out.
 *
 * Polling cadence: 2-second interval driven by ``useQuery``'s
 * ``refetchInterval``; the function returns ``false`` for terminal statuses
 * so polling auto-terminates without manual bookkeeping. We additionally
 * cap the total poll attempts via a ``useRef`` counter so a stuck worker
 * doesn't burn battery forever — after the cap we surface a "still
 * preparing" notice with a manual Refresh button.
 *
 * Conflict path: ``POST /me/data-export`` returns 409 ``one-in-flight`` when
 * a queued/running export already exists. We surface that with copy that
 * tells the user to wait it out instead of treating it as an error.
 *
 * AbortController: every ``getExport`` poll threads the React Query
 * ``signal`` into the fetch, so the in-flight network request is aborted
 * cleanly when the component unmounts mid-poll.
 */

import * as React from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Clock,
  Download,
  FileArchive,
  FileCode2,
  Hourglass,
  Loader2,
  TimerOff,
} from "lucide-react";
import { toast } from "sonner";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { DataExport } from "@arena/shared-types";
import { ApiError, account } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { SectionLabel } from "./AccountView";

const POLL_INTERVAL_MS = 2_000;
const POLL_MAX_ITERATIONS = 60;

function formatExpiry(iso: string | null | undefined): string {
  if (!iso) return "";
  const expires = new Date(iso).getTime();
  if (!Number.isFinite(expires)) return "";
  const remainingMs = expires - Date.now();
  if (remainingMs <= 0) return "expired";
  const days = Math.floor(remainingMs / (24 * 60 * 60 * 1000));
  const hours = Math.floor((remainingMs % (24 * 60 * 60 * 1000)) / (60 * 60 * 1000));
  if (days >= 1) {
    return `${days}d ${hours}h`;
  }
  const minutes = Math.floor((remainingMs % (60 * 60 * 1000)) / (60 * 1000));
  return `${hours}h ${minutes}m`;
}

function describeStatus(status: DataExport["status"]): string {
  switch (status) {
    case "queued":
      return "Queued — your export will start shortly.";
    case "running":
      return "Preparing your export. This usually takes under a minute.";
    case "ready":
      return "Ready to download.";
    case "failed":
      return "The export job failed.";
    case "expired":
      return "The download window for this export has expired.";
  }
}

type StatusVisualTone = "neutral" | "success" | "warning" | "danger";

interface StatusVisual {
  Icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  tone: StatusVisualTone;
}

function statusVisual(status: DataExport["status"]): StatusVisual {
  switch (status) {
    case "queued":
      return { Icon: Hourglass, tone: "neutral" };
    case "running":
      return { Icon: Loader2, tone: "neutral" };
    case "ready":
      return { Icon: FileArchive, tone: "success" };
    case "failed":
      return { Icon: AlertTriangle, tone: "danger" };
    case "expired":
      return { Icon: TimerOff, tone: "warning" };
  }
}

function toneClass(tone: StatusVisualTone): string {
  switch (tone) {
    case "success":
      return "text-[var(--color-primary)]";
    case "warning":
      return "text-[var(--color-warning)]";
    case "danger":
      return "text-[var(--color-danger)]";
    case "neutral":
    default:
      return "text-[var(--color-muted-foreground)]";
  }
}

export interface DataExportPanelProps {
  /** Disabled while the account is in the deletion-grace window — every
   *  mutating call returns 403 in that state and the panel surfaces a
   *  helper line so the CTA isn't a click trap. */
  locked: boolean;
}

export function DataExportPanel({ locked }: DataExportPanelProps) {
  const queryClient = useQueryClient();
  const [exportId, setExportId] = React.useState<string | null>(null);
  const [conflict, setConflict] = React.useState(false);
  // Manual poll cap — React Query has no built-in attempt budget, so we
  // count refetches via ``onSuccess`` / ``onError`` and freeze polling
  // once we cross the threshold. ``useRef`` (not ``useState``) so we don't
  // re-render on every poll — the surface text doesn't depend on the count.
  const pollCountRef = React.useRef(0);
  const [pollExhausted, setPollExhausted] = React.useState(false);

  const query = useQuery({
    queryKey: ["me", "data-export", exportId],
    enabled: !!exportId,
    queryFn: async ({ signal }) => {
      if (!exportId) throw new Error("export id unavailable");
      pollCountRef.current += 1;
      if (pollCountRef.current >= POLL_MAX_ITERATIONS) {
        setPollExhausted(true);
      }
      return account.getExport(exportId, signal);
    },
    refetchInterval: (q) => {
      if (pollExhausted) return false;
      const data = q.state.data;
      if (!data) return POLL_INTERVAL_MS;
      if (data.status === "queued" || data.status === "running") {
        return POLL_INTERVAL_MS;
      }
      return false;
    },
    // ``staleTime`` matches the poll interval so the seeded "queued"
    // envelope written by ``requestMutation.onSuccess`` is treated as
    // fresh — without it, RQ fires an extra fetch the instant the query
    // is enabled and the user never gets to see the initial queued state.
    staleTime: POLL_INTERVAL_MS,
    // Treat transient 5xx as worth retrying inside the poll budget but bail
    // out on 4xx (e.g. the export was scrubbed) so a permanent failure
    // doesn't loop forever.
    retry: (failureCount, err) => {
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        return false;
      }
      return failureCount < 2;
    },
    refetchOnWindowFocus: false,
  });

  const current = query.data ?? null;

  const requestMutation = useMutation({
    mutationFn: () => account.requestExport(),
    onSuccess(data) {
      // Seed the query cache so the first render after kickoff already
      // shows the queued envelope — no flash of "no exports yet".
      queryClient.setQueryData(["me", "data-export", data.id], data);
      pollCountRef.current = 0;
      setPollExhausted(false);
      setConflict(false);
      setExportId(data.id);
      toast.success("Export requested. We'll notify you when it's ready.");
    },
    onError(err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        // Another export is already queued/running. We don't know its id
        // (the 409 body intentionally doesn't leak unrelated metadata), so
        // we surface a "wait for the existing one" notice. The next render
        // after a successful kickoff will replace this state.
        setConflict(true);
        return;
      }
      const message =
        err instanceof ApiError
          ? err.message || "Couldn't request an export."
          : "Couldn't request an export.";
      toast.error(message);
    },
  });

  function handleRefresh() {
    if (!exportId) return;
    pollCountRef.current = 0;
    setPollExhausted(false);
    void query.refetch();
  }

  const isPolling =
    current !== null && (current.status === "queued" || current.status === "running");

  return (
    <section aria-labelledby="export-heading" className="space-y-6">
      <header>
        <SectionLabel>data</SectionLabel>
        <h2 id="export-heading" className="mt-1 text-lg font-semibold">
          Download your data
        </h2>
        <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
          We bundle your sessions, prompts, file changes, command runs,
          submissions, badges, and consent history into a portable ZIP.
        </p>
      </header>

      <div
        className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4"
        data-testid="export-card"
      >
        {!current ? (
          <div className="space-y-3" data-testid="export-empty">
            <div className="flex items-center gap-3 rounded-md border border-dashed border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-6 text-sm text-[var(--color-muted-foreground)]">
              <FileArchive
                className="size-5 text-[var(--color-muted-foreground)]"
                aria-hidden
              />
              <p>
                No exports yet. Hit the button below to start one — it takes
                under a minute for most accounts.
              </p>
            </div>
            {conflict ? (
              <p
                role="status"
                className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-xs text-[var(--color-muted-foreground)]"
                data-testid="export-conflict"
              >
                Another export is in flight. Wait for it to finish, then
                request a new one if you need an even fresher snapshot.
              </p>
            ) : null}
            <Button
              onClick={() => requestMutation.mutate()}
              disabled={requestMutation.isPending || locked}
              data-testid="request-export"
            >
              {requestMutation.isPending ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : null}
              {requestMutation.isPending ? "Starting…" : "Request export"}
            </Button>
          </div>
        ) : (
          <div className="space-y-3" data-testid="export-active">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <SectionLabel>
                {"status · "}
                <span data-testid="export-status">{current.status}</span>
              </SectionLabel>
              <p className="font-mono text-[11px] text-[var(--color-muted-foreground)]">
                requested {new Date(current.requested_at).toLocaleString()}
              </p>
            </div>

            <p className="text-sm" aria-live="polite" data-testid="export-status-line">
              {(() => {
                const visual = statusVisual(current.status);
                const iconClass = `size-4 ${toneClass(visual.tone)} ${
                  isPolling ? "animate-spin" : ""
                }`;
                const VisualIcon = visual.Icon;
                return (
                  <span className="inline-flex items-center gap-2">
                    <VisualIcon className={iconClass} aria-hidden />
                    {describeStatus(current.status)}
                  </span>
                );
              })()}
            </p>

            {current.status === "ready" && current.download_url ? (
              <div className="flex flex-wrap items-center gap-3" data-testid="export-ready">
                <Button asChild>
                  <a
                    href={current.download_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    download
                    data-testid="export-download"
                  >
                    <Download className="size-4" aria-hidden />
                    Download
                  </a>
                </Button>
                {current.expires_at ? (
                  <p className="inline-flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
                    <Clock className="size-3" aria-hidden />
                    Expires in {formatExpiry(current.expires_at)}.
                  </p>
                ) : null}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => requestMutation.mutate()}
                  disabled={requestMutation.isPending || locked}
                >
                  Request a fresh export
                </Button>
              </div>
            ) : null}

            {current.status === "failed" ? (
              <div className="space-y-3" data-testid="export-failed">
                <p
                  role="alert"
                  className="rounded-md border border-[oklch(from_var(--color-danger)_l_c_h/0.5)] bg-[oklch(from_var(--color-danger)_l_c_h/0.08)] px-3 py-2 text-xs text-[var(--color-danger)]"
                >
                  {current.error ?? "The export job failed."}
                </p>
                <Button
                  onClick={() => requestMutation.mutate()}
                  disabled={requestMutation.isPending || locked}
                >
                  Try again
                </Button>
              </div>
            ) : null}

            {current.status === "expired" ? (
              <div className="space-y-3" data-testid="export-expired">
                <p className="text-xs text-[var(--color-muted-foreground)]">
                  The download window for this export has aged out. Request a
                  new one for a fresh signed URL.
                </p>
                <Button
                  onClick={() => requestMutation.mutate()}
                  disabled={requestMutation.isPending || locked}
                >
                  Request export
                </Button>
              </div>
            ) : null}

            {pollExhausted && isPolling ? (
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-xs text-[var(--color-muted-foreground)]">
                  We&rsquo;ve been polling for a while — feel free to check
                  back later, or refresh now.
                </p>
                <Button variant="secondary" size="sm" onClick={handleRefresh}>
                  Refresh status
                </Button>
              </div>
            ) : null}
          </div>
        )}
      </div>

      {/* P1-6 — per-submission replay artefact callout. The artefact is
          minted per graded submission and downloaded from the share
          dropdown on the report page (the dropdown owns the loading-state
          UX and the share-token forwarding). We surface a discoverability
          card here so users hunting in the Data tab learn the artefact
          exists without us duplicating the download mechanics. */}
      <aside
        aria-labelledby="replay-aside-heading"
        className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
        data-testid="replay-aside"
      >
        <SectionLabel id="replay-aside-heading">
          per-submission replay
        </SectionLabel>
        <div className="mt-2 flex items-start gap-3">
          <FileCode2
            className="mt-0.5 size-5 shrink-0 text-[var(--color-muted-foreground)]"
            aria-hidden
          />
          <div className="space-y-1.5">
            <p className="text-sm text-[var(--color-foreground)]">
              Every graded submission carries a deterministic, signed{" "}
              <span className="font-medium">replay artefact</span> — the
              full supervision-event stream plus the score envelope.
            </p>
            <p className="text-xs text-[var(--color-muted-foreground)]">
              Open any report, click <span className="font-mono">Share →
              Download replay (JSON / ZIP)</span> to grab it. The ZIP also
              bundles a self-contained <span className="font-mono">verify.html</span>
              {" "}so a recruiter can re-derive the signature offline.
            </p>
            <p className="pt-1 text-xs">
              <Link
                href="/profile/me"
                className="font-medium text-[var(--color-primary)] underline-offset-2 hover:underline"
              >
                See your mission history →
              </Link>
            </p>
          </div>
        </div>
      </aside>
    </section>
  );
}
