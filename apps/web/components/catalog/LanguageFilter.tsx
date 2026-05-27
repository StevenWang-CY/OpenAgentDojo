"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import type { MissionLanguage } from "@arena/shared-types";

interface LanguageFilterProps {
  active: MissionLanguage | "all";
  onChange(next: MissionLanguage | "all"): void;
  /** Set of languages present in the loaded catalog. Languages outside this
   *  set are still rendered (so a returning user always sees the canonical
   *  ``All · TypeScript · Python · Go`` strip) but disabled visually. */
  available?: ReadonlySet<MissionLanguage>;
}

/** P1-1 — language filter chip strip rendered above the mission grid.
 *
 *  Mirrors the visual aesthetic of ``CategoryChips``: a tab-list of
 *  lowercase, code-comment-style buttons in a small rounded shell. The
 *  active state inverts foreground/background so it reads like a
 *  highlighted segmented control without needing an extra surface token.
 */
const LANGUAGE_OPTIONS: readonly (MissionLanguage | "all")[] = [
  "all",
  "typescript",
  "python",
  "go",
] as const;

const LANGUAGE_LABEL: Record<MissionLanguage | "all", string> = {
  all: "all",
  typescript: "typescript",
  python: "python",
  go: "go",
};

export function LanguageFilter({
  active,
  onChange,
  available,
}: LanguageFilterProps) {
  return (
    <div
      className="inline-flex flex-wrap items-center gap-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-[3px]"
      role="tablist"
      aria-label="Filter missions by language"
      data-testid="language-filter"
    >
      {LANGUAGE_OPTIONS.map((opt) => {
        const isActive = opt === active;
        const isDisabled =
          opt !== "all" &&
          available !== undefined &&
          !available.has(opt as MissionLanguage);
        return (
          <button
            key={opt}
            type="button"
            role="tab"
            aria-selected={isActive}
            aria-disabled={isDisabled || undefined}
            disabled={isDisabled}
            data-language={opt}
            onClick={() => {
              if (!isDisabled) onChange(opt);
            }}
            className={cn(
              "rounded-md px-3 py-1.5 font-mono text-xs transition-colors duration-150 ease-macos",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]",
              isActive
                ? "bg-[var(--color-foreground)] text-[var(--color-background)]"
                : isDisabled
                  ? "text-[var(--color-muted-foreground)]/40"
                  : "text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]",
            )}
          >
            {LANGUAGE_LABEL[opt]}
          </button>
        );
      })}
    </div>
  );
}
