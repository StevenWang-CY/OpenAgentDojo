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
  FileImage,
  FileText,
  Loader2,
  ShieldCheck,
} from "lucide-react";
import { toast } from "sonner";
import {
  ApiError,
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
}: ShareDropdownProps) {
  const [open, setOpen] = React.useState(false);
  const [pdfStatus, setPdfStatus] = React.useState<RenderState>("idle");
  const [pngStatus, setPngStatus] = React.useState<RenderState>("idle");
  const buttonRef = React.useRef<HTMLButtonElement | null>(null);
  const menuRef = React.useRef<HTMLDivElement | null>(null);

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
          />
          <RenderItem
            kind="png"
            label="Download PNG"
            hint="1200×630 for LinkedIn"
            submissionId={submissionId}
            state={pngStatus}
            setState={setPngStatus}
            icon={FileImage}
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
}: {
  icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  label: string;
  hint?: string;
  onClick(): void;
  spinning?: boolean;
  external?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      disabled={disabled}
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
}: {
  kind: "pdf" | "png";
  label: string;
  hint: string;
  submissionId: string;
  state: RenderState;
  setState(next: RenderState): void;
  icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
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
      const status = await getReportRenderStatus(submissionId, kind, undefined, signal);
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
            undefined,
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
        onClick={() => void forceRerender()}
        className="mr-1 inline-flex size-6 items-center justify-center rounded-md text-[var(--color-muted-foreground)] hover:bg-[var(--color-muted)]"
      >
        <Loader2 className="size-3" aria-hidden />
      </button>
    </div>
  );
}
