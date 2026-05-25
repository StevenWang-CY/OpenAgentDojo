"use client";

import * as React from "react";
import { Search as SearchIcon, X as CloseIcon } from "lucide-react";
import type { SearchMatch, SearchResponse } from "@arena/shared-types";
import { ApiError, searchFiles } from "@/lib/api";
import { cn } from "@/lib/utils";

const DEBOUNCE_MS = 250;

export interface SearchPanelProps {
  sessionId: string;
  open: boolean;
  onClose(): void;
  /**
   * Called when the user picks a match. ``line`` is the 1-indexed line
   * number of the match within the file (the editor uses it for the
   * "jump to match" focus).
   */
  onSelect(path: string, line: number): void;
}

interface GroupedFile {
  path: string;
  matches: SearchMatch[];
}

/**
 * Cmd/Ctrl+Shift+F find-in-files panel (P0-9).
 *
 * Layout:
 *   * Sticky header with the query input, regex toggle, case toggle, glob
 *     filter, and a Close button.
 *   * Result list grouped by file. Each file collapses (default open); each
 *     match shows ``line:col`` and the surrounding line with the match
 *     highlighted.
 *   * Footer with ``N matches in M files · 12ms`` and a truncation chip.
 *
 * Lifecycle:
 *   * Every keystroke debounces by 250ms.
 *   * Each new query aborts the previous in-flight fetch via AbortController.
 *   * Mounted at the workspace level (rendered conditionally on
 *     ``workspaceStore.searchPanelOpen``) so unmounting cancels any
 *     hanging request.
 */
export function SearchPanel({
  sessionId,
  open,
  onClose,
  onSelect,
}: SearchPanelProps) {
  const [query, setQuery] = React.useState("");
  const [debounced, setDebounced] = React.useState("");
  const [regex, setRegex] = React.useState(false);
  const [caseSensitive, setCaseSensitive] = React.useState(false);
  const [glob, setGlob] = React.useState("");
  const [result, setResult] = React.useState<SearchResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<{
    code?: string;
    message: string;
  } | null>(null);
  // Refresh tick so the user can re-issue the same query — without it,
  // ``debounced`` would be unchanged and the effect wouldn't re-run.
  const [refreshTick, setRefreshTick] = React.useState(0);

  React.useEffect(() => {
    const h = window.setTimeout(() => setDebounced(query), DEBOUNCE_MS);
    return () => window.clearTimeout(h);
  }, [query]);

  React.useEffect(() => {
    if (!open) return;
    if (!debounced) {
      setResult(null);
      setError(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    setLoading(true);
    setError(null);

    searchFiles(
      sessionId,
      {
        query: debounced,
        regex,
        case_sensitive: caseSensitive,
        glob: glob.trim() || null,
        max_results: 200,
      },
      { signal: controller.signal },
    )
      .then((res) => {
        if (cancelled) return;
        setResult(res);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError) {
          const code =
            err.body && typeof err.body.detail === "object"
              ? ((err.body.detail as { code?: string }).code ?? undefined)
              : undefined;
          const messageFromBody =
            err.body && typeof err.body.detail === "object"
              ? ((err.body.detail as { message?: string }).message ??
                err.message)
              : err.message;
          setError({ code, message: messageFromBody });
        } else {
          setError({ message: "Search failed." });
        }
        setResult(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [debounced, regex, caseSensitive, glob, sessionId, open, refreshTick]);

  const grouped: GroupedFile[] = React.useMemo(() => {
    if (!result) return [];
    const map = new Map<string, SearchMatch[]>();
    for (const match of result.matches) {
      const arr = map.get(match.path);
      if (arr) {
        arr.push(match);
      } else {
        map.set(match.path, [match]);
      }
    }
    return Array.from(map.entries()).map(([path, matches]) => ({
      path,
      matches,
    }));
  }, [result]);

  if (!open) return null;

  return (
    <section
      aria-label="Find in files"
      data-testid="search-panel"
      className="flex h-full flex-col bg-[var(--color-surface)]"
    >
      <header className="flex flex-col gap-2 border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-2.5">
        <div className="flex items-center gap-2">
          <SearchIcon
            className="size-4 text-[var(--color-muted-foreground)]"
            aria-hidden
          />
          <input
            autoFocus
            type="text"
            placeholder="Find in files…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              // Re-issue the same query on Enter — common pattern when the
              // user has already typed a query and just wants to refresh
              // after editing a file.
              if (e.key === "Enter") {
                e.preventDefault();
                setDebounced(query);
                setRefreshTick((t) => t + 1);
              }
            }}
            className="w-full bg-transparent font-mono text-sm outline-none placeholder:text-[var(--color-muted-foreground)]"
            data-testid="search-panel-input"
          />
          <button
            type="button"
            aria-label="Close find-in-files"
            onClick={onClose}
            className="rounded-md p-1 text-[var(--color-muted-foreground)] transition-colors hover:bg-[var(--color-muted)] hover:text-[var(--color-foreground)]"
          >
            <CloseIcon className="size-3.5" aria-hidden />
          </button>
        </div>
        <div className="flex items-center gap-1.5 text-[11px] font-mono">
          <ToggleChip
            active={regex}
            onClick={() => setRegex((v) => !v)}
            label="regex"
            testid="search-panel-regex"
          />
          <ToggleChip
            active={caseSensitive}
            onClick={() => setCaseSensitive((v) => !v)}
            label="Aa"
            ariaLabel="Case sensitive"
            testid="search-panel-case"
          />
          <input
            type="text"
            placeholder="glob: src/**"
            value={glob}
            onChange={(e) => setGlob(e.target.value)}
            data-testid="search-panel-glob"
            className="ml-auto w-32 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-0.5 text-[11px] outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
          />
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-auto">
        {loading && (
          <p className="p-3 font-mono text-xs text-[var(--color-muted-foreground)]">
            {"// searching…"}
          </p>
        )}
        {error && (
          <p
            role="alert"
            data-testid="search-panel-error"
            className="p-3 font-mono text-xs text-[var(--color-danger)]"
          >
            {error.code === "invalid_regex"
              ? `// invalid regex: ${error.message}`
              : error.code === "search_timeout"
                ? "// search timed out — try a narrower pattern"
                : `// ${error.message}`}
          </p>
        )}
        {!loading && !error && result && grouped.length === 0 && (
          <p className="p-3 font-mono text-xs text-[var(--color-muted-foreground)]">
            {"// no matches"}
          </p>
        )}
        {!loading && !error && !result && (
          <p className="p-3 font-mono text-xs text-[var(--color-muted-foreground)]">
            {"// type to find in files"}
          </p>
        )}
        <ul className="divide-y divide-[var(--color-border)]">
          {grouped.map((group) => (
            <li key={group.path} className="py-1">
              <p className="px-3 py-1 font-mono text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]">
                {group.path}{" "}
                <span className="font-normal normal-case text-[var(--color-muted-foreground)]/70">
                  ({group.matches.length})
                </span>
              </p>
              <ul>
                {group.matches.map((m, i) => (
                  <li key={`${group.path}-${m.line_number}-${i}`}>
                    <button
                      type="button"
                      onClick={() => onSelect(m.path, m.line_number)}
                      data-testid="search-panel-match"
                      className="flex w-full items-baseline gap-2 px-3 py-1 text-left font-mono text-xs hover:bg-[var(--color-muted)]"
                    >
                      <span className="w-10 shrink-0 text-right text-[var(--color-muted-foreground)]">
                        {m.line_number}
                      </span>
                      <span className="truncate">
                        {renderHighlightedLine(m)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      </div>
      {result && (
        <footer
          data-testid="search-panel-footer"
          className="flex items-center gap-2 border-t border-[var(--color-border)] px-3 py-1.5 font-mono text-[11px] text-[var(--color-muted-foreground)]"
        >
          <span>
            {result.total} match{result.total === 1 ? "" : "es"} in{" "}
            {grouped.length} file{grouped.length === 1 ? "" : "s"}
          </span>
          <span className="text-[var(--color-muted-foreground)]/60">·</span>
          <span>{result.duration_ms}ms</span>
          {result.truncated && (
            <span
              data-testid="search-panel-truncated"
              className="ml-auto rounded-full border border-[var(--color-warning)] px-2 py-0.5 text-[10px] uppercase tracking-wide text-[var(--color-warning)]"
            >
              truncated
            </span>
          )}
        </footer>
      )}
    </section>
  );
}

interface ToggleChipProps {
  active: boolean;
  onClick: () => void;
  label: string;
  ariaLabel?: string;
  testid?: string;
}

function ToggleChip({
  active,
  onClick,
  label,
  ariaLabel,
  testid,
}: ToggleChipProps) {
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={ariaLabel}
      onClick={onClick}
      data-testid={testid}
      className={cn(
        "rounded-md border px-1.5 py-0.5 text-[11px] transition-colors",
        active
          ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
          : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-muted-foreground)] hover:bg-[var(--color-muted)]",
      )}
    >
      {label}
    </button>
  );
}

/**
 * Render the matched line with the substring highlight. The match offsets
 * come from ripgrep as character indices into the (possibly truncated) line
 * text; we clamp defensively before slicing in case a future ripgrep release
 * shifts the offsets relative to our truncation.
 */
function renderHighlightedLine(match: SearchMatch): React.ReactNode {
  const { line_text, match_start, match_end } = match;
  const start = Math.max(0, Math.min(match_start, line_text.length));
  const end = Math.max(start, Math.min(match_end, line_text.length));
  if (start === end) {
    return <span>{line_text}</span>;
  }
  return (
    <>
      <span>{line_text.slice(0, start)}</span>
      <mark className="rounded-sm bg-[var(--color-warning)]/30 px-0.5 text-[var(--color-foreground)]">
        {line_text.slice(start, end)}
      </mark>
      <span>{line_text.slice(end)}</span>
    </>
  );
}

