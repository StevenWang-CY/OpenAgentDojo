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
  auth: "Auth",
  testing: "Testing",
  security: "Security",
  frontend: "Frontend",
  api: "API",
  database: "Database",
  refactoring: "Refactoring",
  "agent-safety": "Agent safety",
  review: "Review",
  debugging: "Debugging",
};

export function CategoryChips({ available, active, onChange }: CategoryChipsProps) {
  const options: ("all" | MissionCategory)[] = ["all", ...available];

  return (
    <div
      className="flex flex-wrap items-center gap-2"
      role="tablist"
      aria-label="Filter missions by category"
    >
      {options.map((opt) => {
        const isActive = opt === active;
        const label = opt === "all" ? "All categories" : CATEGORY_LABEL[opt];
        return (
          <button
            key={opt}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(opt)}
            className={cn(
              "rounded-full border px-3 py-1 text-xs font-medium transition-colors duration-150 ease-macos",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] focus-visible:ring-offset-2",
              isActive
                ? "border-[var(--color-primary)] bg-[oklch(from_var(--color-primary)_l_c_h/0.15)] text-[var(--color-primary)]"
                : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-muted-foreground)] hover:border-[var(--color-border-strong)] hover:text-[var(--color-foreground)]"
            )}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
