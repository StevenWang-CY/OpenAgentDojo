import type { Metadata } from "next";
import type { Mission, MissionLanguage } from "@arena/shared-types";
import { Clock3 } from "lucide-react";
import { env } from "@/lib/env";
import { PUBLIC_REPO_URL } from "@/components/catalog/ComingSoonCard";

export const metadata: Metadata = {
  title: "Roadmap — OpenAgentDojo",
  description:
    "What is shipping next in the OpenAgentDojo mission catalog. Dated placeholders for the upcoming wave of agent-supervision missions across TypeScript, Python, and Go.",
};

// Re-fetch on every request so a newly published roadmap entry surfaces
// without a deploy. The endpoint is cheap (one DB hit + a YAML parse).
export const dynamic = "force-dynamic";
export const revalidate = 0;

type FetchOutcome =
  | { kind: "ok"; missions: Mission[] }
  | { kind: "offline"; reason: string };

async function fetchRoadmap(): Promise<FetchOutcome> {
  const url = `${env.apiBaseUrl}/api/v1/missions?include=upcoming`;
  try {
    const res = await fetch(url, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!res.ok) {
      // P1 audit fix — the reason string is kept private to server logs
      // so anonymous visitors never see the backend URL or status code
      // (which previously leaked into the rendered DOM). The DOM render
      // path consumes ``kind: "offline"`` only and renders a friendly,
      // URL-free message.
      const reason = `HTTP ${res.status} from ${url}`;
      console.error("roadmap fetch failed:", reason);
      return { kind: "offline", reason };
    }
    const payload = (await res.json()) as Mission[];
    return { kind: "ok", missions: payload };
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    console.error("roadmap fetch failed:", reason);
    return { kind: "offline", reason };
  }
}

const LANGUAGE_CHIP_LABEL: Record<MissionLanguage, string> = {
  typescript: "ts",
  python: "py",
  go: "go",
};

function formatTargetDate(iso: string | null | undefined): string {
  if (!iso) return "soon";
  return iso.slice(0, 10);
}

export default async function RoadmapPage() {
  const outcome = await fetchRoadmap();
  const missions = outcome.kind === "ok" ? outcome.missions : [];
  const shippedStandard = missions.filter(
    (m) => m.status === "shipped" && m.kind !== "tutorial",
  );
  const shippedTutorial = missions.filter(
    (m) => m.status === "shipped" && m.kind === "tutorial",
  );
  const upcoming = missions
    .filter((m) => m.status === "coming_soon")
    .sort((a, b) => {
      const ad = a.target_release_date ?? "9999-12-31";
      const bd = b.target_release_date ?? "9999-12-31";
      return ad.localeCompare(bd);
    });

  return (
    <section className="mx-auto w-full max-w-3xl px-6 py-14">
      <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
        <span className="text-[var(--color-primary)]">{"//"}</span> roadmap
      </p>
      <h1 className="mt-1.5 text-3xl font-semibold tracking-tight">
        What is shipping next.
      </h1>
      <p className="mt-3 max-w-2xl text-[var(--color-muted-foreground)]">
        Missions are written deliberately. Each one targets a specific
        failure mode in agent-assisted coding — the kind a careful
        supervisor catches and a casual one doesn&rsquo;t. Below is the
        live count, plus a dated list of what we are building next.
      </p>

      <p className="mt-6 font-mono text-xs text-[var(--color-muted-foreground)]">
        {outcome.kind === "ok" ? (
          <>
            {`// ${shippedStandard.length} standard mission${
              shippedStandard.length === 1 ? "" : "s"
            }`}
            {shippedTutorial.length > 0
              ? ` + ${shippedTutorial.length} tutorial`
              : ""}
            {" shipped"}
          </>
        ) : (
          <>{"// catalog count unavailable — see the GitHub repo"}</>
        )}
      </p>

      {outcome.kind === "offline" ? (
        <p
          data-testid="roadmap-offline-notice"
          className="mt-6 rounded-lg border border-dashed border-[var(--color-border)] bg-[var(--color-surface)] px-5 py-4 font-mono text-xs text-[var(--color-muted-foreground)]"
          aria-live="polite"
        >
          {"// roadmap is offline — try the GitHub repo for the latest"}
        </p>
      ) : upcoming.length === 0 ? (
        <p className="mt-6 font-mono text-xs text-[var(--color-muted-foreground)]">
          {"// no upcoming missions on the roadmap yet — check the repo for the latest plan."}
        </p>
      ) : (
        <ol
          className="mt-8 space-y-3"
          aria-label="Upcoming missions on the roadmap"
        >
          {upcoming.map((mission) => {
            const language = LANGUAGE_CHIP_LABEL[mission.language] ?? "ts";
            const dateLabel = formatTargetDate(mission.target_release_date);
            return (
              <li
                key={mission.id}
                data-testid="roadmap-entry"
                data-mission-id={mission.id}
                className="rounded-lg border border-dashed border-[var(--color-border)] bg-[var(--color-surface)]/70 px-5 py-4"
              >
                <div className="flex items-center justify-between font-mono text-[11px] text-[var(--color-muted-foreground)]">
                  <span className="inline-flex items-center gap-1.5">
                    <Clock3 className="size-3" aria-hidden />
                    {dateLabel}
                  </span>
                  <span aria-label={`Language: ${mission.language}`}>
                    {`// ${language}`}
                  </span>
                </div>
                <p className="mt-2 text-[15px] font-semibold leading-snug tracking-tight text-[var(--color-foreground)]">
                  {mission.title}
                </p>
                {mission.short_description ? (
                  <p className="mt-1 text-[13px] leading-normal text-[var(--color-muted-foreground)]">
                    {mission.short_description}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}

      <p className="mt-10 font-mono text-xs text-[var(--color-muted-foreground)]">
        Want to follow along, or send feedback on what should ship next?{" "}
        <a
          href={PUBLIC_REPO_URL}
          target="_blank"
          rel="noreferrer noopener"
          className="underline underline-offset-2 hover:text-[var(--color-foreground)]"
        >
          watch repo ↗
        </a>
      </p>

      <p
        data-testid="roadmap-authoring-note"
        className="mt-4 font-mono text-[11px] text-[var(--color-muted-foreground)]"
      >
        {"// want to author a mission? see "}
        <code className="text-[var(--color-foreground)]">
          scripts/mission-template/
        </code>
      </p>
    </section>
  );
}
