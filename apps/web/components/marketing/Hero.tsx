"use client";

import Link from "next/link";
import { ArrowRight, Sparkles } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, listMissions } from "@/lib/api";
import { Button } from "@/components/ui/Button";

export function Hero() {
  // Reuse the same useQuery key as ScenarioCarousel so React Query
  // serves the same response from the cache and we don't double-fetch.
  const { data } = useQuery({
    queryKey: ["missions"],
    queryFn: ({ signal }) => listMissions(signal),
    retry: (failureCount, e) => {
      if (e instanceof ApiError && e.status === 0) return false;
      return failureCount < 1;
    },
    staleTime: 60_000,
  });
  const missionCount = Array.isArray(data) ? data.length : null;
  return (
    <section
      aria-labelledby="hero-heading"
      className="relative isolate overflow-hidden border-b border-[var(--color-border)]"
    >
      {/* Subtle layered gradient backdrop — soft enough to read text crisply. */}
      <div
        aria-hidden
        className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_top,_oklch(from_var(--color-primary)_l_c_h/0.18),_transparent_60%)]"
      />
      <div
        aria-hidden
        className="absolute inset-x-0 -top-24 -z-10 h-72 bg-[radial-gradient(ellipse_at_center,_oklch(from_var(--color-accent)_l_c_h/0.18),_transparent_70%)] blur-3xl"
      />

      <div className="mx-auto flex max-w-5xl flex-col items-center px-6 py-24 text-center sm:py-32">
        <span className="inline-flex items-center gap-2 rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1 text-xs font-medium text-[var(--color-muted-foreground)] shadow-soft">
          <Sparkles className="size-3.5 text-[var(--color-primary)]" aria-hidden />
          Hybrid-simulation training for AI supervisors
        </span>

        <h1
          id="hero-heading"
          className="mt-6 text-balance text-4xl font-semibold tracking-tight sm:text-5xl"
        >
          <span className="text-[var(--color-foreground)]">Hello, OpenAgentDojo.</span>
          <span className="block text-2xl font-normal text-[var(--color-muted-foreground)] sm:text-3xl">
            Learn to supervise coding agents that quietly get it wrong.
          </span>
        </h1>

        <p className="mt-6 max-w-2xl text-balance text-base text-[var(--color-muted-foreground)] sm:text-lg">
          Real repositories. A deliberately-flawed agent. Hidden tests that
          punish lazy review. OpenAgentDojo grades the <em>process</em> of supervision
          — prompting, context, diff review, verification, correction — not
          just the final patch.
        </p>

        <div className="mt-9 flex flex-col items-center gap-3 sm:flex-row">
          <Button asChild size="lg">
            <Link href="/missions">
              Browse Missions
              <ArrowRight className="size-4" />
            </Link>
          </Button>
          <Button asChild size="lg" variant="secondary">
            <Link href="/auth/sign-in">Create account</Link>
          </Button>
        </div>

        <dl className="mt-12 grid grid-cols-1 gap-6 text-sm sm:grid-cols-3">
          {[
            {
              label: "Curated missions",
              value: missionCount === null ? "—" : String(missionCount),
            },
            { label: "Rubric dimensions", value: "7" },
            { label: "Deterministic grading", value: "Always" },
          ].map((item) => (
            <div
              key={item.label}
              className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-left shadow-soft"
            >
              <dt className="text-xs uppercase tracking-wide text-[var(--color-muted-foreground)]">
                {item.label}
              </dt>
              <dd className="mt-1 text-xl font-semibold tracking-tight">
                {item.value}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
