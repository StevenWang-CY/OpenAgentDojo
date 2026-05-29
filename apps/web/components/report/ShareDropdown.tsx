"use client";

/**
 * P0-11 — Share dropdown on the report header.
 *
 * Replaces the single "Share report" button with a small disclosure
 * menu that surfaces four affordances:
 *
 *   - Copy share link  (existing 30-day signed URL)
 *   - Download PDF     (NEW — Playwright Chromium render, signed R2 URL)
 *   - Download PNG     (NEW — 1200×630, LinkedIn-sized social card)
 *   - Open verification page →  (NEW — public /verify URL)
 *
 * No Radix dependency — the dropdown is a small inline disclosure with
 * keyboard support (Escape closes, focus returns to trigger). The PDF
 * and PNG buttons enter a polling state until the worker reports
 * ``status === "ready"``; clicking again during polling re-polls
 * instead of re-enqueueing.
 */

import * as React from "react";
import {
  ChevronDown,
  Copy,
  Download,
  ExternalLink,
  FileArchive,
  FileCode2,
  FileImage,
  FileText,
  Loader2,
  RotateCw,
  ShieldCheck,
} from "lucide-react";
import { toast } from "sonner";
import {
  ApiError,
  downloadReplayJson,
  downloadReplayZip,
  forceReportRender,
  getReportRenderStatus,
  type ReportRender,
} from "@/lib/api";
import { track } from "@/lib/telemetry";
import { cn } from "@/lib/utils";

interface ShareDropdownProps {
  submissionId: string;
  /** Triggered by "Copy share link" — delegates to ReportView's
   *  existing mint+copy flow so we don't duplicate the 30-day token
   *  expiry handling. */
  onCopyLink(): void;
  sharing: boolean;
  /** P1-6 — when the report is being viewed via a public share link, the
   *  dropdown forwards the token on the replay download calls so the
   *  backend's owner-OR-share auth matrix lets them through (redacted
   *  prompt payloads for share-token viewers). ``null`` is the owner
   *  path — the cookie alone authorises the call. */
  share?: string | null;
}

/**
 * P1-6 — discriminator for the ``replay_export_failed`` ``error_class``
 * dimension. Kept narrow on purpose so the telemetry funnel can bucket
 * common failure modes (the long tail collapses into ``unknown``).
 */
type ReplayErrorClass =
  | "network_error"
  | "not_found"
  | "not_graded"
  | "verify_secret_unavailable"
  | "unknown";

function classifyReplayError(err: unknown): ReplayErrorClass {
  if (err instanceof ApiError) {
    if (err.status === 0) return "network_error";
    if (err.status === 404) {
      // The backend uses a single 404 for both "submission unknown" and
      // "submission not graded yet / tutorial"; the body's ``detail``
      // string discriminates. We surface the more specific class when the
      // signal is present so the funnel can tell "wrong id" from "user
      // clicked replay before grading completed".
      const detail =
        err.body && typeof err.body.detail === "string"
          ? err.body.detail.toLowerCase()
          : "";
      if (detail.includes("not graded") || detail.includes("tutorial")) {
        return "not_graded";
      }
      return "not_found";
    }
    // FE remediation — explicit bucket for 503s from the verify-secret
    // dependency. Previously these compressed into ``unknown`` and the
    // funnel couldn't tell a one-off backend dip from a sustained
    // outage. The backend returns 503 from the replay endpoint when the
    // ``REPLAY_VERIFY_SECRET`` env wiring is missing (the artefact
    // can't be signed) or when its KMS dependency is unreachable.
    if (err.status === 503) return "verify_secret_unavailable";
  }
  return "unknown";
}

const POLL_INTERVAL_MS = 5_000;
const POLL_MAX_ATTEMPTS = 24; // ~2 min ceiling at 5s intervals

/**
 * Open ``url`` in a new tab without navigating the current one. We use an
 * anchor element (rather than ``window.open``) so popup blockers treat the
 * navigation as user-initiated — it runs on the click handler stack — and
 * so the report page itself isn't replaced by the PDF/PNG.
 */
function openInNewTab(url: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener,noreferrer";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function ShareDropdown({
  submissionId,
  onCopyLink,
  sharing,
  share = null,
}: ShareDropdownProps) {
  const [open, setOpen] = React.useState(false);
  const [pdfStatus, setPdfStatus] = React.useState<RenderState>("idle");
  const [pngStatus, setPngStatus] = React.useState<RenderState>("idle");
  // P1-6 — independent loading flags for the two replay variants so the user
  // can request the JSON while the ZIP is mid-build (or vice versa) without
  // either spinner clobbering the other. The menu items remain clickable so
  // a stuck request can be retried via a second click — the network layer
  // de-dupes via the browser's HTTP cache for the JSON variant.
  const [replayJsonBusy, setReplayJsonBusy] = React.useState(false);
  const [replayZipBusy, setReplayZipBusy] = React.useState(false);
  const buttonRef = React.useRef<HTMLButtonElement | null>(null);
  const menuRef = React.useRef<HTMLDivElement | null>(null);

  // ── P1-6 — replay download click handlers ────────────────────────────────
  //
  // Both handlers share the same shape:
  //   1. fire ``replay_export_requested`` (intent funnel)
  //   2. await the API call, branching on success/failure
  //   3. fire the matching ``_succeeded`` / ``_failed`` event
  //   4. surface a toast in the appropriate tone
  // The dropdown is intentionally NOT closed on click so a user who wants
  // both variants can grab both without re-opening the menu.

  const onDownloadJson = React.useCallback(async () => {
    if (replayJsonBusy) return;
    setReplayJsonBusy(true);
    track("replay_export_requested", { submission_id: submissionId, kind: "json" });
    // FE remediation — ``downloadReplayJson`` now performs the file save
    // itself and returns ``{bytes, filename}`` so the on-wire bytes match
    // what hit disk. We deliberately do NOT re-stringify a parsed JS
    // object here — that destroyed the canonical key ordering / whitespace
    // the backend serialises with and broke replay-hash verification for
    // anyone who downloaded the file and compared it byte-for-byte.
    const work: Promise<string> = (async () => {
      const result = await downloadReplayJson(submissionId, {
        share: share ?? undefined,
      });
      track("replay_export_succeeded", {
        submission_id: submissionId,
        kind: "json",
        bytes: result.bytes,
      });
      return result.filename;
    })();
    toast.promise(work, {
      loading: "Preparing replay JSON…",
      success: (name) => `Downloaded ${name}`,
      error: (err: unknown) => replayErrorMessage(err),
    });
    try {
      await work;
    } catch (err) {
      track("replay_export_failed", {
        submission_id: submissionId,
        kind: "json",
        error_class: classifyReplayError(err),
      });
    } finally {
      setReplayJsonBusy(false);
    }
  }, [submissionId, share, replayJsonBusy]);

  const onDownloadZip = React.useCallback(async () => {
    if (replayZipBusy) return;
    setReplayZipBusy(true);
    track("replay_export_requested", { submission_id: submissionId, kind: "zip" });
    const work: Promise<string> = (async () => {
      const result = await downloadReplayZip(submissionId, {
        share: share ?? undefined,
      });
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
      track("replay_export_failed", {
        submission_id: submissionId,
        kind: "zip",
        error_class: classifyReplayError(err),
      });
    } finally {
      setReplayZipBusy(false);
    }
  }, [submissionId, share, replayZipBusy]);

  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      const target = e.target as Node | null;
      if (!target) return;
      if (menuRef.current?.contains(target)) return;
      if (buttonRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid="share-dropdown-trigger"
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium",
          "transition-[background-color,box-shadow] duration-150 ease-macos hover:bg-[var(--color-muted)]",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-background)]",
          open && "bg-[var(--color-muted)] shadow-soft",
        )}
      >
        <Download className="size-3.5" aria-hidden />
        Share
        <ChevronDown
          className={cn(
            "size-3 text-[var(--color-muted-foreground)] transition-transform duration-180 ease-macos",
            open && "rotate-180",
          )}
          aria-hidden
        />
      </button>

      {open ? (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Share report"
          className="absolute right-0 top-full z-30 mt-1 min-w-[240px] origin-top-right rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-1 shadow-elevated"
          style={{
            animation:
              "fadeSlideIn 160ms var(--ease-macos, cubic-bezier(0.32, 0.72, 0, 1))",
          }}
        >
          <MenuItem
            icon={sharing ? Loader2 : Copy}
            label="Copy share link"
            hint="30-day signed URL"
            spinning={sharing}
            onClick={() => {
              setOpen(false);
              onCopyLink();
            }}
          />
          <RenderItem
            kind="pdf"
            label="Download PDF"
            hint="print-fidelity, embeds verification hash"
            submissionId={submissionId}
            state={pdfStatus}
            setState={setPdfStatus}
            icon={FileText}
            share={share}
          />
          <RenderItem
            kind="png"
            label="Download PNG"
            hint="1200×630 for LinkedIn"
            submissionId={submissionId}
            state={pngStatus}
            setState={setPngStatus}
            icon={FileImage}
            share={share}
          />
          <Separator />
          {/* P1-6 — replay artefact downloads. Both items stay clickable
              for share-token viewers (the backend serves a redacted
              payload). 404s — tutorial / ungraded submissions — are
              surfaced via the toast set up in the click handler. */}
          <MenuItem
            icon={replayJsonBusy ? Loader2 : FileCode2}
            label="Download replay (JSON)"
            hint="canonical artefact, signed"
            spinning={replayJsonBusy}
            disabled={replayJsonBusy}
            data-testid="replay-json-item"
            onClick={() => {
              void onDownloadJson();
            }}
          />
          <MenuItem
            icon={replayZipBusy ? Loader2 : FileArchive}
            label="Download replay (ZIP)"
            hint="bundle + verify.html + README"
            spinning={replayZipBusy}
            disabled={replayZipBusy}
            data-testid="replay-zip-item"
            onClick={() => {
              void onDownloadZip();
            }}
          />
          <Separator />
          <MenuItem
            icon={ShieldCheck}
            label="Open verification page"
            hint="public /verify URL"
            external
            onClick={() => {
              setOpen(false);
              const target = `/verify/${encodeURIComponent(submissionId)}`;
              window.open(target, "_blank", "noopener,noreferrer");
            }}
          />
        </div>
      ) : null}
    </div>
  );
}

/**
 * Translate a replay-download failure into a user-facing toast message.
 * Centralised so both onDownloadJson and onDownloadZip render the same
 * copy for the same conditions; the wording mirrors the design's spec
 * ("Replay not available for this submission" for 404s, the raw
 * ApiError message otherwise).
 */
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

// ── Menu primitives ────────────────────────────────────────────────────────

type RenderState =
  | "idle"
  | "queued"
  | "running"
  | "ready"
  | "failed";

function MenuItem({
  icon: Icon,
  label,
  hint,
  onClick,
  spinning = false,
  external = false,
  disabled = false,
  "data-testid": testId,
}: {
  icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  label: string;
  hint?: string;
  onClick(): void;
  spinning?: boolean;
  external?: boolean;
  disabled?: boolean;
  /** Optional ``data-testid`` for unit-test selection. The menu items have
   *  ambiguous labels (two "Download ..." entries) so a stable testid is the
   *  clean way to address them in tests without brittle text matching. */
  "data-testid"?: string;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      className={cn(
        "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-[var(--color-foreground)]",
        "transition-colors duration-150 ease-macos hover:bg-[var(--color-muted)] focus-visible:outline-none focus-visible:bg-[var(--color-muted)]",
        disabled && "cursor-not-allowed opacity-60",
      )}
    >
      <Icon
        className={cn(
          "size-3.5 text-[var(--color-muted-foreground)]",
          spinning && "animate-spin",
        )}
        aria-hidden
      />
      <span className="flex-1">
        <span>{label}</span>
        {hint ? (
          <span className="ml-1.5 font-mono text-[10px] text-[var(--color-muted-foreground)]">
            ({hint})
          </span>
        ) : null}
      </span>
      {external ? (
        <ExternalLink
          className="size-3 text-[var(--color-muted-foreground)]"
          aria-hidden
        />
      ) : null}
    </button>
  );
}

function Separator() {
  return <div className="my-1 h-px bg-[var(--color-border)]" aria-hidden />;
}

function RenderItem({
  kind,
  label,
  hint,
  submissionId,
  state,
  setState,
  icon: Icon,
  share,
}: {
  kind: "pdf" | "png";
  label: string;
  hint: string;
  submissionId: string;
  state: RenderState;
  setState(next: RenderState): void;
  icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  /** P1-6 — share token for an anonymous viewer on /report/{id}?share=…;
   *  threaded into the render-status poll so the backend's owner-OR-share
   *  auth lets the download through instead of 401-ing. ``null`` /
   *  ``undefined`` is the owner path (cookie alone authorises). */
  share?: string | null;
}) {
  const busy = state === "queued" || state === "running";

  // Refs survive re-renders so cleanup can find the running interval/abort
  // controller no matter how many times the component re-renders during a
  // single polling lifecycle.
  const intervalRef = React.useRef<number | null>(null);
  const abortRef = React.useRef<AbortController | null>(null);
  const attemptsRef = React.useRef(0);
  const startMsRef = React.useRef(0);
  const stateRef = React.useRef(state);

  // Keep a ref copy of state so async callbacks (which would otherwise
  // close over a stale snapshot) can read the current value cheaply.
  React.useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const stopPolling = React.useCallback(() => {
    if (intervalRef.current !== null) {
      window.clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    attemptsRef.current = 0;
  }, []);

  // Single mount-time cleanup so a click → unmount sequence (the user
  // navigates away while polling) doesn't leak the interval or the
  // in-flight fetch.
  React.useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  async function ensureRender(): Promise<void> {
    // Re-clicking the menu item while a poll is in flight should be a
    // no-op rather than spawning a second interval.
    if (intervalRef.current !== null || stateRef.current === "queued" || stateRef.current === "running") {
      return;
    }

    startMsRef.current = Date.now();
    attemptsRef.current = 0;
    abortRef.current = new AbortController();
    const signal = abortRef.current.signal;

    try {
      const status = await getReportRenderStatus(submissionId, kind, share ?? undefined, signal);
      if ("ready" in status && status.ready) {
        setState("ready");
        track("report_render_requested", { kind, cache_hit: true });
        track("report_render_succeeded", {
          submission_id: submissionId,
          kind,
          ms_to_ready: Date.now() - startMsRef.current,
        });
        openInNewTab(status.url);
        stopPolling();
        return;
      }
      const row = status as ReportRender;
      track("report_render_requested", { kind, cache_hit: false });
      setState(row.status as RenderState);
      if (row.status === "failed") {
        toast.error(row.error || `Could not render ${kind.toUpperCase()}.`);
        track("report_render_failed", {
          submission_id: submissionId,
          kind,
          error_class: "render_failed",
        });
        stopPolling();
        return;
      }

      // Poll the same endpoint until ready / failed / max attempts.
      intervalRef.current = window.setInterval(async () => {
        attemptsRef.current += 1;
        if (attemptsRef.current > POLL_MAX_ATTEMPTS) {
          stopPolling();
          setState("failed");
          toast.error(
            `Rendering ${kind.toUpperCase()} took too long — please retry.`,
          );
          track("report_render_failed", {
            submission_id: submissionId,
            kind,
            error_class: "polling_timeout",
          });
          return;
        }

        // Fresh controller for each poll tick so an unmount/abort aborts
        // exactly the in-flight request, not future ones too.
        const tickController = new AbortController();
        abortRef.current = tickController;
        try {
          const next = await getReportRenderStatus(
            submissionId,
            kind,
            share ?? undefined,
            tickController.signal,
          );
          if ("ready" in next && next.ready) {
            setState("ready");
            track("report_render_succeeded", {
              submission_id: submissionId,
              kind,
              ms_to_ready: Date.now() - startMsRef.current,
            });
            openInNewTab(next.url);
            stopPolling();
            return;
          }
          const r = next as ReportRender;
          setState(r.status as RenderState);
          if (r.status === "failed") {
            toast.error(r.error || `Could not render ${kind.toUpperCase()}.`);
            track("report_render_failed", {
              submission_id: submissionId,
              kind,
              error_class: "render_failed",
            });
            stopPolling();
          }
        } catch (err) {
          // AbortError on unmount is expected; swallow it so we don't
          // emit a spurious failure event.
          if (
            err instanceof DOMException && err.name === "AbortError"
          ) {
            return;
          }
          stopPolling();
          setState("failed");
          if (err instanceof ApiError) {
            toast.error(err.message);
          } else {
            toast.error(`Lost connection while rendering ${kind.toUpperCase()}.`);
          }
          track("report_render_failed", {
            submission_id: submissionId,
            kind,
            error_class:
              err instanceof ApiError && err.status === 0
                ? "network_error"
                : err instanceof ApiError
                  ? "render_failed"
                  : "network_error",
          });
        }
      }, POLL_INTERVAL_MS);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        return;
      }
      stopPolling();
      setState("failed");
      if (err instanceof ApiError) {
        toast.error(err.message);
      } else {
        toast.error(`Could not start render of ${kind.toUpperCase()}.`);
      }
      track("report_render_failed", {
        submission_id: submissionId,
        kind,
        error_class:
          err instanceof ApiError && err.status === 0
            ? "network_error"
            : err instanceof ApiError
              ? "render_failed"
              : "network_error",
      });
    }
  }

  async function forceRerender() {
    try {
      const row = await forceReportRender(submissionId, kind);
      setState(row.status as RenderState);
      toast.success(`Re-rendering ${kind.toUpperCase()} …`);
      // Kick off polling.
      void ensureRender();
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        toast.error(
          (err.body as { detail?: { message?: string } } | null)?.detail?.message ||
            "Force re-render cap reached for the day.",
        );
      } else if (err instanceof ApiError) {
        toast.error(err.message);
      } else {
        toast.error(`Force-render of ${kind.toUpperCase()} failed.`);
      }
    }
  }

  return (
    <div className="flex items-center gap-1">
      <MenuItem
        icon={busy ? Loader2 : Icon}
        label={busy ? `${label}…` : label}
        hint={hint}
        spinning={busy}
        onClick={() => void ensureRender()}
      />
      <button
        type="button"
        title="Force re-render"
        aria-label={`Force re-render ${kind.toUpperCase()}`}
        data-testid={`force-rerender-${kind}`}
        onClick={() => void forceRerender()}
        className="mr-1 inline-flex size-6 items-center justify-center rounded-md text-[var(--color-muted-foreground)] hover:bg-[var(--color-muted)]"
      >
        {/* FE remediation — was Loader2 (a spinner), which read as "render
            is in progress" rather than "re-trigger the render". RotateCw
            is the standard refresh affordance and matches the lucide-react
            set already pulled in for this file. */}
        <RotateCw className="size-3" aria-hidden />
      </button>
    </div>
  );
}
