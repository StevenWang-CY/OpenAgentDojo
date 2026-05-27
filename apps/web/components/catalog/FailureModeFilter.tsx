"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/** P1-1 — closed failure-mode vocabulary mirrored from
 *  ``apps/api/app/missions/manifest.py::_FAILURE_MODE_TAGS``. Friendly
 *  labels are FE-only; keep the keys exact so the value passed to the
 *  ``?tags=`` query and the on-card ``failure_mode_id`` always match. */
export const FAILURE_MODE_TAGS = [
  "checks_presence_not_expiration",
  "overfitted_visible_test",
  "wrong_layer_committed",
  "missing_regression_test",
  "race_condition",
  "context_dropped",
  "error_wrapped_swallowed",
  "dependency_misuse",
  "security_check_removed",
  "typecheck_ignored",
  "api_contract_drift",
  "excessive_rewrite",
  "goroutine_leak",
] as const;

export type FailureModeTag = (typeof FAILURE_MODE_TAGS)[number];

export const FAILURE_MODE_LABEL: Record<FailureModeTag, string> = {
  checks_presence_not_expiration: "Checks presence, not expiration",
  overfitted_visible_test: "Overfitted to the visible test",
  wrong_layer_committed: "Patched the wrong layer",
  missing_regression_test: "Missing regression test",
  race_condition: "Race condition",
  context_dropped: "Context dropped",
  error_wrapped_swallowed: "Error wrapped and swallowed",
  dependency_misuse: "Dependency misuse",
  security_check_removed: "Security check removed",
  typecheck_ignored: "Typecheck ignored",
  api_contract_drift: "API contract drift",
  excessive_rewrite: "Excessive rewrite",
  goroutine_leak: "Goroutine leak",
};

interface FailureModeFilterProps {
  active: FailureModeTag | "all";
  onChange(next: FailureModeTag | "all"): void;
  /** Optional whitelist; tags outside this set are hidden so the dropdown
   *  only ever surfaces failure modes that exist in the currently-loaded
   *  catalog. Pass ``undefined`` (or omit) to render the full vocabulary. */
  available?: ReadonlySet<string>;
}

/** P1-1 — failure-mode dropdown rendered above the mission grid.
 *
 *  Uses a native ``<select>`` for accessibility + zero-dep keyboard
 *  navigation; the dojo aesthetic ships through the ``// failure mode ▾``
 *  prefix and the lowercase, code-comment-style labels. */
export function FailureModeFilter({
  active,
  onChange,
  available,
}: FailureModeFilterProps) {
  const options = React.useMemo<FailureModeTag[]>(() => {
    if (!available) return [...FAILURE_MODE_TAGS];
    return FAILURE_MODE_TAGS.filter((tag) => available.has(tag));
  }, [available]);

  return (
    <label
      className="inline-flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 font-mono text-xs"
      data-testid="failure-mode-filter"
    >
      <span
        className="text-[var(--color-muted-foreground)]"
        aria-hidden
      >
        {"// failure mode"}
      </span>
      <select
        aria-label="Filter missions by failure mode"
        value={active}
        onChange={(e) => onChange(e.target.value as FailureModeTag | "all")}
        className={cn(
          "appearance-none bg-transparent pr-3 text-[var(--color-foreground)] outline-none",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] rounded-sm",
        )}
      >
        <option value="all">all</option>
        {options.map((tag) => (
          <option key={tag} value={tag}>
            {FAILURE_MODE_LABEL[tag]}
          </option>
        ))}
      </select>
      <span aria-hidden className="text-[var(--color-muted-foreground)]">
        ▾
      </span>
    </label>
  );
}
