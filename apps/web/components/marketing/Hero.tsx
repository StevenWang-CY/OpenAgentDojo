import Link from "next/link";
import { Button } from "@/components/ui/Button";

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

export function Hero() {
  return (
    <section
      aria-labelledby="hero-heading"
      className="relative isolate overflow-hidden border-b border-[var(--color-border)]"
    >
      {/* Subtle grid backdrop — discipline, not decoration. Mask-faded so it
          never competes with the type. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 bg-[linear-gradient(to_right,_oklch(from_var(--color-foreground)_l_c_h/0.04)_1px,_transparent_1px),_linear-gradient(to_bottom,_oklch(from_var(--color-foreground)_l_c_h/0.04)_1px,_transparent_1px)] bg-[size:56px_56px] [mask-image:radial-gradient(ellipse_at_top,_black_30%,_transparent_75%)]"
      />

      <div className="mx-auto max-w-6xl px-6 pt-20 pb-16 sm:pt-24">
        <p className="font-mono text-xs text-[var(--color-muted-foreground)]">
          <span className="text-[var(--color-primary)]">//</span> supervision
          training · OpenAgentDojo v1
        </p>

        <h1
          id="hero-heading"
          className="mt-4 max-w-[820px] text-balance text-4xl font-semibold leading-[1.05] tracking-tight sm:text-5xl md:text-[52px]"
        >
          Patches that look right, aren&rsquo;t.
          <br />
          <em className="font-medium not-italic text-[var(--color-muted-foreground)]">
            Train the eye that catches them.
          </em>
        </h1>

        <p className="mt-5 max-w-[620px] text-base leading-relaxed text-pretty text-[var(--color-muted-foreground)] sm:text-[17px]">
          Real repositories. A deliberately-flawed agent. Hidden tests that
          punish lazy review. OpenAgentDojo grades the{" "}
          <em className="rounded-sm bg-[oklch(from_var(--color-warning)_l_c_h/0.18)] px-1 not-italic text-[var(--color-foreground)]">
            process
          </em>{" "}
          of supervision &mdash; prompting, context, diff review,
          verification, correction &mdash; not just the final patch.
        </p>

        <div className="mt-7 flex flex-wrap items-center gap-3">
          <Button asChild>
            <Link href="/missions">
              Browse missions
              <span aria-hidden className="transition-transform duration-150 ease-macos group-hover:translate-x-0.5">
                →
              </span>
            </Link>
          </Button>
          <Button asChild variant="secondary">
            <Link href="/auth/sign-in">Create account</Link>
          </Button>
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
                  // Right-border between cells, suppressed on the last item
                  // in the visible row. The grid changes at sm/lg breakpoints
                  // so we lean on Tailwind's :nth-child arithmetic for the
                  // mid-row dividers; the last-of-type rule wipes the very
                  // last cell unconditionally.
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
