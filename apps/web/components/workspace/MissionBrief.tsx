"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ScrollArea } from "@/components/ui/ScrollArea";
import { cn } from "@/lib/utils";

interface MissionBriefProps {
  /** Mission brief in markdown. */
  brief: string;
  title?: string;
  className?: string;
}

export function MissionBrief({ brief, title, className }: MissionBriefProps) {
  return (
    <ScrollArea className={cn("h-full", className)}>
      <div className="p-4">
        {title ? (
          <header className="mb-2">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]">
              Mission brief
            </p>
            <h2 className="mt-1 text-sm font-semibold leading-tight tracking-tight">
              {title}
            </h2>
          </header>
        ) : null}
        <article className="prose prose-sm prose-neutral max-w-none dark:prose-invert prose-headings:tracking-tight prose-headings:font-semibold prose-pre:bg-[var(--color-muted)]">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{brief}</ReactMarkdown>
        </article>
      </div>
    </ScrollArea>
  );
}
