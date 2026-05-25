import { notFound } from "next/navigation";
import { headers } from "next/headers";
import { env } from "@/lib/env";
import { ReportPrintView } from "@/components/report/ReportPrintView";

/**
 * P0-11 — Internal print-mode route consumed by the report-render
 * worker.
 *
 * The worker visits this URL with an ``X-Render-Token`` header (HMAC
 * over ``submission_id + render_id`` signed with ``VERIFY_SECRET``).
 * Without the header, the route 404s — a casual visitor never lands on
 * the un-chromed print surface. The worker's Chromium then prints the
 * page to PDF / PNG.
 *
 * The route is server-rendered with the full submission payload so the
 * worker doesn't have to wait for client hydration. ``revalidate: 0``
 * because the page consumes a one-shot token and never caches.
 */
export const revalidate = 0;
export const dynamic = "force-dynamic";

interface PrintPageProps {
  params: Promise<{ submissionId: string }>;
  searchParams: Promise<{ kind?: "pdf" | "png"; token?: string }>;
}

interface Submission {
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

async function fetchSubmissionForPrint(
  submissionId: string,
  token: string,
): Promise<Submission | null> {
  // The print route forwards the worker's X-Render-Token header so the
  // backend can authorise the surface. We use the verify endpoint as
  // the source-of-record minus the envelope-only fields — the worker
  // wants the FULL report payload to render the deep page.
  try {
    const resp = await fetch(
      `${env.apiBaseUrl}/api/v1/reports/${encodeURIComponent(submissionId)}/print`,
      {
        headers: { "X-Render-Token": token },
        cache: "no-store",
      },
    );
    if (!resp.ok) return null;
    return (await resp.json()) as Submission;
  } catch {
    return null;
  }
}

export default async function ReportPrintPage({
  params,
  searchParams,
}: PrintPageProps) {
  const { submissionId } = await params;
  const { kind = "pdf", token } = await searchParams;

  // The worker MUST provide the token either via the query string (our
  // own internal call) or via the X-Render-Token header (the canonical
  // contract). Either is acceptable; the BE's /reports/{id}/print
  // endpoint is the one that verifies.
  const headerList = await headers();
  const headerToken = headerList.get("x-render-token") ?? token ?? "";
  if (!headerToken) {
    notFound();
  }

  const submission = await fetchSubmissionForPrint(submissionId, headerToken);
  if (!submission) {
    notFound();
  }

  return <ReportPrintView submission={submission} kind={kind} />;
}
