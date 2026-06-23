import { notFound } from "next/navigation";
import { env } from "@/lib/env";
import type { VerifyEnvelope } from "@/lib/api";
import { VerifyPageBody } from "@/components/verify/VerifyPageBody";

interface VerifyPageProps {
  params: Promise<{ submissionId: string }>;
}

/**
 * Public verification page (P0-11).
 *
 * Server-rendered with a short ISR window. A graded submission's envelope
 * is immutable *once it exists*, so a successful response is cheap to
 * cache — but a verify URL can be requested BEFORE the envelope exists
 * (a recruiter follows the link the instant grading is still in flight).
 * If we cached that negative permanently (``revalidate: false`` +
 * ``force-cache``), ``notFound()`` would pin a stale 404 to the path for
 * good: the same URL would keep serving 404 even after grading lands.
 *
 * So we gate caching on ``response.ok`` — a successful immutable envelope
 * is cached at the data layer (``force-cache``), but a 404/error is
 * fetched ``no-store`` so it never sticks, and the page-level
 * ``revalidate`` is a finite window so a once-404'd render recovers.
 *
 * No (app) or (marketing) layout — the route has its own minimal
 * layout in ``apps/web/app/verify/layout.tsx``.
 */
export const revalidate = 60;
export const dynamicParams = true;

async function fetchEnvelope(
  submissionId: string,
): Promise<VerifyEnvelope | null> {
  // Server-side fetch — uses the API origin directly. We can't pick the
  // cache policy until we've seen the status, so issue the request with
  // ``no-store`` first: this guarantees a 404 (envelope not yet minted) is
  // never written to the data cache. Only a confirmed-OK response is worth
  // caching, which we do with a second immutable ``force-cache`` read.
  try {
    const url = `${env.apiBaseUrl}/api/v1/verify/${encodeURIComponent(submissionId)}`;
    const probe = await fetch(url, { cache: "no-store" });
    if (!probe.ok) return null;
    // Envelope exists and is immutable — re-read through the data cache so
    // subsequent renders within the ISR window are served from cache.
    const cached = await fetch(url, {
      next: { revalidate: false },
      cache: "force-cache",
    });
    const resp = cached.ok ? cached : probe;
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
