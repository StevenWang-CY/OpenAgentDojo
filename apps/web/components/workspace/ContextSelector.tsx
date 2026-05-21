"use client";

import * as React from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface ContextSelectorProps {
  selected: string[];
  onRemove(path: string): void;
  onClear?(): void;
  className?: string;
}

/**
 * Compact pill list of paths the user has marked as context for the next prompt.
 * Drives `workspaceStore.selectedContext`.
 */
export function ContextSelector({
  selected,
  onRemove,
  onClear,
  className,
}: ContextSelectorProps) {
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]">
          Selected context · {selected.length}
        </p>
        {selected.length > 0 && onClear ? (
          <button
            type="button"
            onClick={onClear}
            className="rounded-sm text-[10px] text-[var(--color-muted-foreground)] underline-offset-2 transition-colors duration-150 ease-macos hover:text-[var(--color-foreground)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
          >
            clear
          </button>
        ) : null}
      </div>
      {selected.length === 0 ? (
        <p className="text-xs text-[var(--color-muted-foreground)]">
          Pick files in the tree to include in your next prompt.
        </p>
      ) : (
        <ul className="flex flex-wrap gap-1.5">
          {selected.map((path) => (
            <li key={path}>
              <span className="inline-flex items-center gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-0.5 font-mono text-[11px]">
                <span className="max-w-[180px] truncate" title={path}>
                  {path}
                </span>
                <button
                  type="button"
                  onClick={() => onRemove(path)}
                  aria-label={`Remove ${path} from context`}
                  className="rounded p-0.5 text-[var(--color-muted-foreground)] transition-colors duration-150 ease-macos hover:bg-[var(--color-muted)] hover:text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
                >
                  <X className="size-3" />
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
