import type { Metadata } from "next";
import { env } from "@/lib/env";
import type { Submission } from "@arena/shared-types";
import { ReportView } from "@/components/report/ReportView";

interface PageProps {
  params: Promise<{ submissionId: string }>;
  searchParams: Promise<{ share?: string | string[] }>;
}

async function fetchReportForMetadata(
  submissionId: string,
  share: string | null
): Promise<Submission | null> {
  try {
    const url = new URL(
      `${env.apiBaseUrl}/api/v1/reports/${encodeURIComponent(submissionId)}`
    );
    if (share) url.searchParams.set("share", share);
    const res = await fetch(url.toString(), {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as Submission;
  } catch {
    return null;
  }
}

function readShare(value: string | string[] | undefined): string | null {
  if (typeof value === "string" && value.length > 0) return value;
  if (Array.isArray(value) && value.length > 0 && value[0]) return value[0];
  return null;
}

export async function generateMetadata({
  params,
  searchParams,
}: PageProps): Promise<Metadata> {
  const { submissionId } = await params;
  const sp = await searchParams;
  const share = readShare(sp.share);
  const submission = await fetchReportForMetadata(submissionId, share);

  if (!submission) {
    return {
      title: "Score report",
      description: "AgentSupervisor Arena — graded supervision attempt.",
    };
  }

  const score = submission.total_score;
  const strengths = submission.score_report.strengths;
  const summary =
    strengths.length > 0
      ? strengths.slice(0, 2).join(" · ")
      : "Process-driven supervision grading across 7 rubric dimensions.";

  const title = `Score ${score}/100 · Arena`;
  // Preserve the share token on the OG card route so the edge runtime fetch
  // succeeds for unauthenticated viewers loading the public share link.
  const ogImageUrl = new URL(
    `/report/${encodeURIComponent(submissionId)}/opengraph-image`,
    "https://placeholder.invalid"
  );
  if (share) ogImageUrl.searchParams.set("share", share);
  const ogImage = `${ogImageUrl.pathname}${ogImageUrl.search}`;

  return {
    title,
    description: summary,
    openGraph: {
      title,
      description: summary,
      type: "article",
      images: [{ url: ogImage, width: 1200, height: 630, alt: title }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description: summary,
      images: [ogImage],
    },
  };
}

export default async function ReportPage({ params, searchParams }: PageProps) {
  const { submissionId } = await params;
  const sp = await searchParams;
  const share = readShare(sp.share);
  return <ReportView submissionId={submissionId} share={share} />;
}
