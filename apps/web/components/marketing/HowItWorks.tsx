import { ClipboardList, GitPullRequest, ShieldCheck, Trophy } from "lucide-react";
import type { LucideIcon } from "lucide-react";

interface Step {
  icon: LucideIcon;
  title: string;
  body: string;
}

const STEPS: Step[] = [
  {
    icon: ClipboardList,
    title: "Pick a mission",
    body: "Start from a real repo with a real bug. Read the brief, choose context, and prompt the agent.",
  },
  {
    icon: GitPullRequest,
    title: "Review the patch",
    body: "The agent applies a plausible — but subtly wrong — change. Open the diff. Run the tests. Push back.",
  },
  {
    icon: ShieldCheck,
    title: "Correct and verify",
    body: "Edit files, add regression tests, and rerun your checks. Every action streams into your supervision timeline.",
  },
  {
    icon: Trophy,
    title: "Get graded",
    body: "Hidden tests + structural validators score seven supervision dimensions. Earn badges. Share your profile.",
  },
];

export function HowItWorks() {
  return (
    <section
      aria-labelledby="how-heading"
      className="mx-auto max-w-6xl px-6 py-20"
    >
      <header className="max-w-2xl">
        <p className="text-xs uppercase tracking-[0.2em] text-[var(--color-muted-foreground)]">
          How it works
        </p>
        <h2
          id="how-heading"
          className="mt-2 text-3xl font-semibold tracking-tight"
        >
          Four steps. Process over patches.
        </h2>
        <p className="mt-3 text-[var(--color-muted-foreground)]">
          The platform watches how you supervise — not just whether the bug
          goes away. Every prompt, diff, command, and edit feeds the score.
        </p>
      </header>

      <ol className="mt-10 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((step, idx) => {
          const Icon = step.icon;
          return (
            <li
              key={step.title}
              className="relative rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-soft transition-shadow duration-200 ease-macos hover:shadow-elevated"
            >
              <div className="flex items-center justify-between">
                <span
                  aria-hidden
                  className="grid size-9 place-items-center rounded-lg bg-[oklch(from_var(--color-primary)_l_c_h/0.15)] text-[var(--color-primary)]"
                >
                  <Icon className="size-4" />
                </span>
                <span className="font-mono text-xs text-[var(--color-muted-foreground)]">
                  {String(idx + 1).padStart(2, "0")}
                </span>
              </div>
              <h3 className="mt-4 text-sm font-semibold tracking-tight">
                {step.title}
              </h3>
              <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
                {step.body}
              </p>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
