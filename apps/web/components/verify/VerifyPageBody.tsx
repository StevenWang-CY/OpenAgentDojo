"use client";

import * as React from "react";
import Link from "next/link";
import { CheckCircle2, Copy, ShieldAlert, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import type { VerifyEnvelope } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { track } from "@/lib/telemetry";

/**
 * P0-11 — Public verify page body.
 *
 * Intentionally minimal — the page is a credential, not a marketing
 * surface. No images, no external requests, no JS-driven hydration
 * except the "copy hash" affordance and the telemetry ping.
 *
 * Two visual modes:
 *
 *   * ``envelope.proctored === true`` — full "verified credential"
 *     chrome: the green "// verified report" eyebrow, the ShieldCheck
 *     icon, and the "Identity verified" subtitle.
 *   * ``envelope.proctored === false`` — honor-mode attestation:
 *     amber/grey "// honor mode attestation" eyebrow, the ShieldAlert
 *     icon, and an explicit "Self-study attempt — not a verified
 *     credential" subtitle so the page can never be misread as a
 *     proctored credential.
 */
export function VerifyPageBody({ envelope }: { envelope: VerifyEnvelope }) {
  const proctored = envelope.proctored === true;
  // Telemetry — fired exactly once on first mount. The referer host
  // (when present) is the key acquisition signal: did the URL come from
  // a LinkedIn profile, a personal site, or a paste in Slack?
  React.useEffect(() => {
    const referer =
      typeof document !== "undefined" ? document.referrer : "";
    let referer_host: string | null = null;
    try {
      referer_host = referer ? new URL(referer).host : null;
    } catch {
      referer_host = null;
    }
    track("report_verified", {
      submission_id: envelope.submission_id,
      referer_host,
    });
  }, [envelope.submission_id]);

  return (
    <main className="mx-auto flex min-h-dvh max-w-3xl flex-col gap-6 px-6 py-12 font-mono text-sm text-[var(--color-foreground)]">
      <header className="flex items-center justify-between gap-2 text-[10px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
        <span aria-hidden>
          {proctored ? "// verified report" : "// honor mode attestation"}
        </span>
        <span aria-hidden>openagentdojo.app</span>
      </header>

      <section
        aria-label={proctored ? "Verified credential" : "Honor mode attestation"}
        data-testid="verify-mode-banner"
        data-proctored={proctored ? "true" : "false"}
        className={
          proctored
            ? "flex items-center gap-2 rounded-md border border-[oklch(from_var(--color-primary)_l_c_h/0.5)] bg-[oklch(from_var(--color-primary)_l_c_h/0.08)] px-3 py-2 text-[11px] text-[var(--color-foreground)]"
            : "flex items-center gap-2 rounded-md border border-[oklch(from_var(--color-warning)_l_c_h/0.45)] bg-[oklch(from_var(--color-warning)_l_c_h/0.10)] px-3 py-2 text-[11px] text-[var(--color-foreground)]"
        }
      >
        {proctored ? (
          <ShieldCheck
            className="size-4 text-[var(--color-primary)]"
            aria-hidden
          />
        ) : (
          <ShieldAlert
            className="size-4 text-[var(--color-warning)]"
            aria-hidden
          />
        )}
        <span>
          {proctored
            ? "Identity verified"
            : "Self-study attempt — not a verified credential."}
        </span>
      </section>

      <section
        aria-labelledby="verify-score"
        className="flex flex-col items-center gap-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] px-6 py-10 text-center shadow-soft"
      >
        <p
          id="verify-score"
          className="font-mono text-6xl font-semibold tracking-tight tabular-nums"
        >
          {envelope.total_score}
          <span className="text-[var(--color-muted-foreground)] text-3xl">
            {" / "}
            {envelope.effective_max}
          </span>
        </p>
        <p className="text-[13px] leading-relaxed text-[var(--color-muted-foreground)]">
          <span className="font-semibold text-[var(--color-foreground)]">
            {envelope.mission_title || envelope.mission_id}
          </span>
          {" · "}
          <span className="font-mono">{envelope.mission_id}</span>
        </p>
        <p className="text-xs text-[var(--color-muted-foreground)]">
          @{envelope.handle}
          {envelope.display_name ? ` (${envelope.display_name})` : ""}
          {" · attempt "}
          {envelope.attempt_index}
        </p>
      </section>

      <section className="space-y-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
        <ul className="space-y-1.5 text-xs text-[var(--color-muted-foreground)]">
          <li className="flex items-center gap-2">
            {envelope.missed_failure_mode ? (
              <span className="size-3.5 rounded-full bg-[var(--color-danger)]/40" aria-hidden />
            ) : (
              <CheckCircle2
                className="size-3.5 text-[var(--color-success)]"
                aria-hidden
              />
            )}
            <span>
              {envelope.missed_failure_mode
                ? "Missed the mission's failure mode"
                : "Failure mode identified"}
            </span>
          </li>
          {envelope.score_cap_reason === "gave_up" ? (
            <li className="flex items-center gap-2">
              <span className="size-3.5 rounded-full bg-[var(--color-warning)]/50" aria-hidden />
              <span>Score capped at 50 / 100 (gave up)</span>
            </li>
          ) : null}
          <li className="flex items-center gap-2">
            <span aria-hidden className="text-[var(--color-muted-foreground)]">
              ·
            </span>
            <span>
              Graded {formatGradedAt(envelope.graded_at)} · rubric{" "}
              <span className="font-mono">{envelope.rubric_version}</span>
            </span>
          </li>
        </ul>
      </section>

      <section
        aria-labelledby="verify-signature"
        className="space-y-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5"
      >
        <h2
          id="verify-signature"
          className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]"
        >
          <ShieldCheck
            className="size-3.5 text-[var(--color-primary)]"
            aria-hidden
          />
          Server-signed envelope
        </h2>
        <p className="text-xs leading-relaxed text-[var(--color-muted-foreground)]">
          Issued by OpenAgentDojo. This page was rendered from a
          server-signed envelope; it cannot be fabricated client-side.
        </p>
        <dl className="space-y-2 text-[11px]">
          <Row label="verification_hash" value={envelope.verification_hash} />
          <Row
            label="signature"
            value={envelope.verification_signature}
          />
        </dl>
      </section>

      <section className="space-y-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 text-xs leading-relaxed text-[var(--color-muted-foreground)]">
        <p>
          This page intentionally does not show prompts, supervision
          events, or code edits. The full report is available to the
          submission owner from inside OpenAgentDojo.
        </p>
        <p>
          <Link
            href={`/report/${envelope.submission_id}`}
            className="inline-flex items-center gap-1 text-[var(--color-primary)] underline decoration-dotted underline-offset-2 hover:decoration-solid"
          >
            Open full report (auth required)
            <span aria-hidden>→</span>
          </Link>
        </p>
      </section>

      <footer className="text-center text-[10px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
        <span>schema v{envelope.schema_version}</span>
        <span aria-hidden> · </span>
        <Link href="/" className="underline decoration-dotted hover:decoration-solid">
          openagentdojo.app
        </Link>
      </footer>
    </main>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2 font-mono text-[11px]">
      <dt className="text-[var(--color-muted-foreground)]">{label}</dt>
      <dd className="flex max-w-[60%] items-center gap-2 truncate">
        <code className="truncate text-[var(--color-foreground)]" title={value}>
          {abbreviate(value)}
        </code>
        <Button
          variant="ghost"
          size="sm"
          aria-label={`Copy ${label}`}
          onClick={() => {
            void navigator.clipboard?.writeText(value);
            toast.success(`Copied ${label}`);
          }}
          className="size-6 p-0"
        >
          <Copy className="size-3" aria-hidden />
        </Button>
      </dd>
    </div>
  );
}

function abbreviate(hex: string): string {
  if (hex.length <= 18) return hex;
  return `${hex.slice(0, 8)}…${hex.slice(-6)}`;
}

function formatGradedAt(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZoneName: "short",
    });
  } catch {
    return iso;
  }
}
