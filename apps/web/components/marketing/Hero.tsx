import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Hero3D } from "./Hero3D";

/**
 * The seven rubric dimensions and their point weights — mirrors the backend's
 * single-source-of-truth at ``apps/api/app/grading/dimensions.py``. Kept as a
 * literal here (rather than a runtime fetch) because the marketing page must
 * render on a cold server with no backend reachable. A contract test pins
 * the values so they stay in sync; bump both sides together on a re-balance.
 */
const RUBRIC: ReadonlyArray<{ name: string; pts: number }> = [
  { name: "Correctness", pts: 30 },
  { name: "Verification", pts: 15 },
  { name: "Agent review", pts: 15 },
  { name: "Prompt", pts: 10 },
  { name: "Context", pts: 10 },
  { name: "Safety", pts: 10 },
  { name: "Minimality", pts: 10 },
];
const RUBRIC_TOTAL = RUBRIC.reduce((sum, d) => sum + d.pts, 0);

/**
 * Hero — landing top fold.
 *
 * Layout: two-column grid (text left, 3D glass scene right) with the rubric
 * strip spanning full width below. The ambient pastel wash behind the
 * section (radial blobs in the background stack) is what the frosted
 * backdrop-filter on each glass element refracts — that quiet color shift
 * is what gives the scene depth. Removing the blobs makes the glass go
 * dead.
 */
export function Hero() {
  return (
    <section
      aria-labelledby="hero-heading"
      className="hero-wash relative isolate overflow-hidden border-b border-[var(--color-border)]"
    >
      <div className="mx-auto max-w-6xl px-6 pt-20 pb-14 sm:pt-24">
        <div className="grid items-center gap-8 md:min-h-[560px] md:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          {/* LEFT: copy + CTAs */}
          <div>
            <p className="font-mono text-xs text-[var(--color-muted-foreground)]">
              <span className="text-[var(--color-primary)]">//</span>{" "}
              supervision training · OpenAgentDojo v1
            </p>

            <h1
              id="hero-heading"
              className="mt-4 max-w-[14ch] text-4xl font-semibold leading-[1.05] tracking-[-0.025em] sm:text-5xl md:text-[56px]"
            >
              Patches that look right, aren&rsquo;t.
              <span className="font-semibold text-[oklch(60%_0.02_250)]">
                {" "}
                Train the eye that catches them.
              </span>
            </h1>

            <p className="mt-5 max-w-[52ch] text-pretty text-base leading-[1.6] text-[var(--color-muted-foreground)] sm:text-[17px]">
              Real repositories. A deliberately-flawed agent. Hidden tests that
              punish lazy review. OpenAgentDojo grades the{" "}
              <em className="rounded-sm bg-[oklch(from_var(--color-warning)_l_c_h/0.28)] px-1 not-italic text-[var(--color-foreground)]">
                process
              </em>{" "}
              of supervision &mdash; prompting, context, diff review,
              verification, correction &mdash; not just the final patch.
            </p>

            <div className="mt-7 flex flex-wrap items-center gap-3">
              <Button asChild size="lg">
                <Link href="/missions">
                  Browse missions
                  <ArrowRight className="size-4" />
                </Link>
              </Button>
              <Button asChild size="lg" variant="secondary">
                <Link href="/auth/sign-in">Create account</Link>
              </Button>
            </div>
          </div>

          {/* RIGHT: 3D glass scene — purely decorative (aria-hidden in the
              component itself). */}
          <Hero3D />
        </div>

        {/* Rubric strip — replaces the 3 stat-cards. Shows the actual product
            (the deterministic 100-point scoring surface). */}
        <div
          role="group"
          aria-label={`The ${RUBRIC_TOTAL}-point supervision rubric`}
          className="mt-14 overflow-hidden rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] shadow-soft"
        >
          <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-4 py-2.5">
            <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-[var(--color-muted-foreground)]">
              the {RUBRIC_TOTAL}-point rubric
            </span>
            <span className="font-mono text-xs text-[var(--color-muted-foreground)]">
              deterministic · zero LLM on the grading path ·{" "}
              <b className="font-semibold text-[var(--color-foreground)]">
                = {RUBRIC_TOTAL} pts
              </b>
            </span>
          </div>
          <ul className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7">
            {RUBRIC.map((dim, idx) => (
              <li
                key={dim.name}
                className={
                  "border-t border-[var(--color-border)] px-4 py-4 sm:[&:nth-child(4n)]:border-r-0 lg:[&:nth-child(4n)]:border-r lg:[&:nth-child(7)]:border-r-0 " +
                  (idx % 2 === 1 ? "border-r-0 sm:border-r" : "border-r") +
                  " border-[var(--color-border)] sm:[&:nth-child(-n+4)]:border-t-0 lg:[&:nth-child(-n+7)]:border-t-0"
                }
              >
                <div className="font-mono text-[22px] font-semibold leading-none tracking-tight text-[var(--color-foreground)]">
                  {dim.pts}
                  <span className="text-sm font-normal text-[var(--color-muted-foreground)]">
                    {" "}
                    /{dim.pts}
                  </span>
                </div>
                <div className="mt-1.5 text-[13px] text-[var(--color-muted-foreground)]">
                  {dim.name}
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
