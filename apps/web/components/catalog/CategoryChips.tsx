"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import type { MissionCategory } from "@arena/shared-types";

interface CategoryChipsProps {
  available: MissionCategory[];
  active: MissionCategory | "all";
  onChange(category: MissionCategory | "all"): void;
}

const CATEGORY_LABEL: Record<MissionCategory, string> = {
  auth: "auth",
  testing: "testing",
  security: "security",
  frontend: "frontend",
  api: "api",
  database: "database",
  refactoring: "refactoring",
  "agent-safety": "agent-safety",
  review: "review",
  debugging: "debugging",
};

export function CategoryChips({
  available,
  active,
  onChange,
}: CategoryChipsProps) {
  const options: ("all" | MissionCategory)[] = ["all", ...available];

  return (
    <div
      className="inline-flex flex-wrap items-center gap-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-[3px]"
      role="tablist"
      aria-label="Filter missions by category"
    >
      {options.map((opt) => {
        const isActive = opt === active;
        const label = opt === "all" ? "all" : CATEGORY_LABEL[opt];
        return (
          <button
            key={opt}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(opt)}
            className={cn(
              "rounded-md px-3 py-1.5 font-mono text-xs transition-colors duration-150 ease-macos",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]",
              isActive
                ? "bg-[var(--color-foreground)] text-[var(--color-background)]"
                : "text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]",
            )}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
