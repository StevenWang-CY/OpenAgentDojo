"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface IdealSolutionProps {
  /** Markdown body — only shown post-submit. */
  markdown: string;
}

export function IdealSolution({ markdown }: IdealSolutionProps) {
  return (
    <section
      aria-labelledby="ideal-solution-heading"
      className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-soft"
    >
      <h2
        id="ideal-solution-heading"
        className="text-sm font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]"
      >
        Ideal solution
      </h2>
      <div className="prose prose-sm prose-neutral mt-3 max-w-none dark:prose-invert">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
      </div>
    </section>
  );
}
