import { notFound } from "next/navigation";
import { env } from "@/lib/env";
import { VerifyPageBody } from "@/components/verify/VerifyPageBody";

interface VerifyPageProps {
  params: Promise<{ submissionId: string }>;
}

/**
 * Public verification page (P0-11).
 *
 * Server-rendered, ISR with ``revalidate: false`` because a graded
 * submission's envelope is immutable. Static cache wins both for
 * latency and for the "you can hand this URL to a recruiter" contract:
 * the body never changes after the first render.
 *
 * No (app) or (marketing) layout — the route has its own minimal
 * layout in ``apps/web/app/verify/layout.tsx``.
 */
export const revalidate = false;
export const dynamicParams = true;

interface VerifyEnvelope {
  schema_version: number;
  submission_id: string;
  handle: string;
  display_name: string | null;
  mission_id: string;
  mission_title: string;
  mission_version: number;
  rubric_version: string;
  total_score: number;
  effective_max: number;
  missed_failure_mode: boolean;
  score_cap_reason: "gave_up" | null;
  proctored: boolean;
  attempt_index: number;
  graded_at: string;
  canonical_url: string;
  verification_hash: string;
  verification_signature: string;
}

async function fetchEnvelope(
  submissionId: string,
): Promise<VerifyEnvelope | null> {
  // Server-side fetch — uses the API origin directly. Cache: ``force-cache``
  // because the envelope is immutable.
  try {
    const resp = await fetch(
      `${env.apiBaseUrl}/api/v1/verify/${encodeURIComponent(submissionId)}`,
      { next: { revalidate: false }, cache: "force-cache" },
    );
    if (resp.status === 404) return null;
    if (!resp.ok) return null;
    return (await resp.json()) as VerifyEnvelope;
  } catch {
    return null;
  }
}

export default async function VerifyPage({ params }: VerifyPageProps) {
  const { submissionId } = await params;
  const envelope = await fetchEnvelope(submissionId);
  if (!envelope) {
    notFound();
  }
  return <VerifyPageBody envelope={envelope} />;
}

export async function generateMetadata({ params }: VerifyPageProps) {
  const { submissionId } = await params;
  const envelope = await fetchEnvelope(submissionId);
  if (!envelope) {
    return { title: "Report not verified · OpenAgentDojo" };
  }
  return {
    title: `${envelope.total_score} / ${envelope.effective_max} · ${envelope.mission_title} · OpenAgentDojo`,
    description: `Verified OpenAgentDojo submission by @${envelope.handle} on mission ${envelope.mission_id}.`,
    openGraph: {
      title: `${envelope.total_score} / ${envelope.effective_max} · ${envelope.mission_title}`,
      description: `Verified OpenAgentDojo submission by @${envelope.handle}.`,
      type: "article",
    },
    robots: { index: true, follow: true },
  };
}
