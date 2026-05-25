/**
 * P0-11 — print-mode body for the verification artifact pipeline.
 *
 * Rendered by the report-render worker's Chromium against
 * ``/report-print/{submission_id}?token=…&kind=pdf``. Kept deliberately
 * lean — no charts, no client hydration — so the PDF / PNG always
 * looks identical and renders fast.
 */

interface PrintSubmission {
  id: string;
  total_score: number;
  score_report: Record<string, unknown>;
  score_cap_reason: "gave_up" | null;
  verification_hash: string | null;
  verification_signature: string | null;
  critical_moments: Array<Record<string, unknown>>;
  mission_id: string | null;
  ideal_solution: string | null;
  ideal_solution_diff: string | null;
  agent_patch_diff: string | null;
  created_at: string;
}

export function ReportPrintView({
  submission,
  kind,
}: {
  submission: PrintSubmission;
  kind: "pdf" | "png";
}) {
  const effectiveMax =
    typeof submission.score_report?.effective_max === "number"
      ? (submission.score_report.effective_max as number)
      : 100;
  const missionTitle =
    typeof submission.score_report?.mission_title === "string"
      ? (submission.score_report.mission_title as string)
      : (submission.mission_id ?? "Mission");

  const isPng = kind === "png";

  return (
    <div
      data-print-kind={kind}
      style={{
        // Use embedded system fonts (JetBrains Mono + Inter via globals.css).
        // The render worker injects @page CSS via Playwright's pdf() margin
        // option; we keep this CSS focused on the visual layout itself.
        // No max-width on PNG kind — the worker sets a 1200x630 viewport.
        background: "var(--color-background)",
        color: "var(--color-foreground)",
        minHeight: isPng ? "auto" : "100vh",
        padding: isPng ? "60px 80px" : "16mm 18mm",
        fontFamily:
          'ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, monospace',
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: "10px",
          letterSpacing: "0.18em",
          textTransform: "uppercase",
          color: "var(--color-muted-foreground)",
          marginBottom: 28,
        }}
      >
        <span>// verified report</span>
        <span>openagentdojo.app</span>
      </header>

      <section
        aria-labelledby="print-score"
        style={{ textAlign: "center", marginBottom: 32 }}
      >
        <p
          id="print-score"
          style={{
            fontSize: isPng ? "120px" : "84px",
            fontWeight: 700,
            lineHeight: 1,
            letterSpacing: "-0.04em",
            margin: 0,
          }}
        >
          {submission.total_score}
          <span
            style={{
              fontSize: isPng ? "48px" : "32px",
              color: "var(--color-muted-foreground)",
              fontWeight: 500,
            }}
          >
            {" / "}
            {effectiveMax}
          </span>
        </p>
        <p
          style={{
            marginTop: 12,
            fontSize: "16px",
            fontWeight: 600,
          }}
        >
          {missionTitle}
        </p>
        {submission.mission_id ? (
          <p
            style={{
              marginTop: 4,
              fontSize: "12px",
              color: "var(--color-muted-foreground)",
            }}
          >
            {submission.mission_id}
          </p>
        ) : null}
        {submission.score_cap_reason === "gave_up" ? (
          <p
            style={{
              marginTop: 8,
              fontSize: "11px",
              color: "var(--color-warning)",
              fontWeight: 600,
            }}
          >
            Score capped at 50 / 100 (gave up)
          </p>
        ) : null}
      </section>

      {!isPng ? (
        <section
          style={{
            borderTop: "1px solid var(--color-border)",
            paddingTop: 24,
            marginBottom: 24,
          }}
        >
          <h2
            style={{
              fontSize: "11px",
              textTransform: "uppercase",
              letterSpacing: "0.18em",
              color: "var(--color-muted-foreground)",
              marginBottom: 12,
            }}
          >
            Server-signed envelope
          </h2>
          <p style={{ fontSize: "11px", color: "var(--color-muted-foreground)" }}>
            Issued by OpenAgentDojo. Verify at{" "}
            <strong>openagentdojo.app/verify/{submission.id}</strong>
          </p>
          <dl style={{ fontSize: "10px", marginTop: 16 }}>
            <Row label="verification_hash" value={submission.verification_hash} />
            <Row
              label="signature"
              value={submission.verification_signature}
            />
            <Row label="graded_at" value={submission.created_at} />
          </dl>
        </section>
      ) : (
        <section
          style={{
            borderTop: "1px solid var(--color-border)",
            paddingTop: 20,
            fontSize: "12px",
            textAlign: "center",
            color: "var(--color-muted-foreground)",
          }}
        >
          openagentdojo.app/verify/{submission.id.slice(0, 8)}…
        </section>
      )}

      {!isPng && submission.ideal_solution ? (
        <section style={{ marginTop: 32, pageBreakBefore: "always" }}>
          <h2
            style={{
              fontSize: "11px",
              textTransform: "uppercase",
              letterSpacing: "0.18em",
              color: "var(--color-muted-foreground)",
              marginBottom: 16,
            }}
          >
            Ideal solution
          </h2>
          <pre
            style={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              fontSize: "11px",
              lineHeight: 1.6,
              color: "var(--color-foreground)",
              background: "var(--color-surface)",
              padding: 16,
              borderRadius: 8,
            }}
          >
            {submission.ideal_solution}
          </pre>
        </section>
      ) : null}
    </div>
  );
}

function Row({
  label,
  value,
}: {
  label: string;
  value: string | null;
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        gap: 16,
        padding: "4px 0",
      }}
    >
      <dt style={{ color: "var(--color-muted-foreground)" }}>{label}</dt>
      <dd
        style={{
          wordBreak: "break-all",
          textAlign: "right",
          fontFamily:
            'ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, monospace',
          color: "var(--color-foreground)",
        }}
      >
        {value ?? "—"}
      </dd>
    </div>
  );
}
