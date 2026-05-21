import { ImageResponse } from "next/og";
import type { ScoreBreakdown, Submission } from "@arena/shared-types";
import { env } from "@/lib/env";

export const runtime = "edge";
export const alt = "AgentSupervisor Arena — graded supervision report";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

interface OgProps {
  params: Promise<{ submissionId: string }>;
  searchParams: Promise<{ share?: string | string[] }>;
}

function readShare(value: string | string[] | undefined): string | null {
  if (typeof value === "string" && value.length > 0) return value;
  if (Array.isArray(value) && value.length > 0 && value[0]) return value[0];
  return null;
}

/**
 * 1200x630 PNG generated at request-time with the headline score and a tiny
 * radar chart of the 7 rubric dimensions. Uses primitives only — `next/og`
 * implements a subset of CSS, so we avoid Tailwind and stick to inline styles.
 */
export default async function OpenGraphImage({ params, searchParams }: OgProps) {
  const { submissionId } = await params;
  const sp = await searchParams;
  const share = readShare(sp.share);
  const submission = await fetchSubmission(submissionId, share);

  const score = submission?.total_score ?? 0;
  const dims = submission?.score_report?.dimensions;
  const passed = !submission?.score_report?.missed_failure_mode;
  const radar = dims ? radarPolygon(dims, 130) : "";

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          background:
            "linear-gradient(135deg, #0b0f17 0%, #131a26 60%, #1b2238 100%)",
          color: "#f7f8fb",
          fontFamily:
            "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Inter",
          padding: 64,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            fontSize: 22,
            letterSpacing: 4,
            textTransform: "uppercase",
            color: "#a4b0c4",
          }}
        >
          <span
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: "#7c8dff",
              display: "inline-block",
            }}
          />
          AgentSupervisor Arena
        </div>

        <div
          style={{
            display: "flex",
            flex: 1,
            marginTop: 36,
            gap: 56,
            alignItems: "center",
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", flex: 1 }}>
            <div style={{ fontSize: 28, color: "#a4b0c4" }}>Score</div>
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: 12,
                marginTop: 8,
              }}
            >
              <span style={{ fontSize: 168, fontWeight: 700, lineHeight: 1 }}>
                {score}
              </span>
              <span style={{ fontSize: 56, color: "#a4b0c4" }}>/ 100</span>
            </div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                marginTop: 24,
                color: passed ? "#7ec699" : "#ef6f6c",
                fontSize: 24,
                fontWeight: 600,
              }}
            >
              <span
                style={{
                  width: 14,
                  height: 14,
                  borderRadius: 999,
                  background: passed ? "#7ec699" : "#ef6f6c",
                  display: "inline-block",
                }}
              />
              {passed ? "Failure mode identified" : "Failure mode missed"}
            </div>
            <div style={{ marginTop: 24, fontSize: 22, color: "#a4b0c4" }}>
              Process-graded supervision across 7 rubric dimensions.
            </div>
          </div>

          {/* Radar */}
          <div
            style={{
              width: 320,
              height: 320,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              position: "relative",
            }}
          >
            <svg width={320} height={320} viewBox="-160 -160 320 320">
              {[0.25, 0.5, 0.75, 1].map((r) => (
                <polygon
                  key={r}
                  points={ringPoints(130 * r)}
                  fill="none"
                  stroke="#2a3247"
                  strokeWidth={1}
                />
              ))}
              {axisLines(130).map((line, i) => (
                <line
                  key={i}
                  x1={0}
                  y1={0}
                  x2={line.x}
                  y2={line.y}
                  stroke="#2a3247"
                  strokeWidth={1}
                />
              ))}
              {radar ? (
                <polygon
                  points={radar}
                  fill="#7c8dff"
                  fillOpacity={0.35}
                  stroke="#7c8dff"
                  strokeWidth={2}
                />
              ) : null}
            </svg>
          </div>
        </div>

        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            marginTop: 24,
            fontSize: 20,
            color: "#a4b0c4",
          }}
        >
          <span>{env.appUrl.replace(/^https?:\/\//, "")}</span>
          <span style={{ fontFamily: "monospace" }}>{submissionId.slice(0, 8)}</span>
        </div>
      </div>
    ),
    size
  );
}

async function fetchSubmission(
  id: string,
  share: string | null
): Promise<Submission | null> {
  try {
    const url = new URL(
      `${env.apiBaseUrl}/api/v1/reports/${encodeURIComponent(id)}`
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

const DIMENSION_ORDER: (keyof ScoreBreakdown)[] = [
  "final_correctness",
  "verification",
  "agent_review",
  "prompt_quality",
  "context_selection",
  "safety",
  "diff_minimality",
];

/** Build the SVG polygon points for the rubric score radar. */
function radarPolygon(dims: ScoreBreakdown, radius: number): string {
  return DIMENSION_ORDER.map((key, i) => {
    const dim = dims[key];
    const ratio = dim.max > 0 ? dim.score / dim.max : 0;
    const angle = (Math.PI * 2 * i) / DIMENSION_ORDER.length - Math.PI / 2;
    const x = Math.cos(angle) * radius * ratio;
    const y = Math.sin(angle) * radius * ratio;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function ringPoints(radius: number): string {
  return DIMENSION_ORDER.map((_, i) => {
    const angle = (Math.PI * 2 * i) / DIMENSION_ORDER.length - Math.PI / 2;
    return `${(Math.cos(angle) * radius).toFixed(1)},${(Math.sin(angle) * radius).toFixed(1)}`;
  }).join(" ");
}

function axisLines(radius: number): { x: number; y: number }[] {
  return DIMENSION_ORDER.map((_, i) => {
    const angle = (Math.PI * 2 * i) / DIMENSION_ORDER.length - Math.PI / 2;
    return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
  });
}
