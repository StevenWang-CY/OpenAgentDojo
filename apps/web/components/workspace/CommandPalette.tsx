"use client";

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { File as FileIcon, Search as SearchIcon } from "lucide-react";
import type { FileListResponse } from "@arena/shared-types";
import { ApiError, getFileList } from "@/lib/api";
import { cn } from "@/lib/utils";

const RESULT_CAP = 50;
const DEBOUNCE_MS = 120;

export interface CommandPaletteProps {
  sessionId: string;
  open: boolean;
  onOpenChange(open: boolean): void;
  /** Called with the chosen workspace-relative path. */
  onSelect(path: string): void;
}

interface PathRow {
  path: string;
  name: string;
}

/**
 * Cmd/Ctrl+P quick-open file picker (P0-9).
 *
 * Behaviour:
 *   * Modal centered dialog (Radix Dialog).
 *   * Single text input (auto-focused). 120ms debounce on every keystroke.
 *   * Up to 50 results. Arrow up/down navigate; Enter picks; Esc dismisses.
 *   * Mouse hover also moves the highlight so keyboard + mouse stay in sync.
 *   * ``truncated`` flag from the server surfaces a "// some files omitted"
 *     hint at the bottom.
 *
 * We deliberately *don't* depend on ``cmdk`` here — the package isn't on the
 * web app's dependency manifest and the minimal accessible list is ~80 lines.
 * The component owns its own focus management (auto-focus on open, arrow-key
 * navigation, ``aria-activedescendant`` for the screen-reader experience).
 */
export function CommandPalette({
  sessionId,
  open,
  onOpenChange,
  onSelect,
}: CommandPaletteProps) {
  const [query, setQuery] = React.useState("");
  const [debounced, setDebounced] = React.useState("");
  const [rows, setRows] = React.useState<PathRow[]>([]);
  const [truncated, setTruncated] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [active, setActive] = React.useState(0);
  const listRef = React.useRef<HTMLUListElement | null>(null);

  // Reset state every time the palette opens. Without this, a previous
  // search would flash for a frame before the new fetch lands.
  React.useEffect(() => {
    if (open) {
      setQuery("");
      setDebounced("");
      setRows([]);
      setTruncated(false);
      setError(null);
      setActive(0);
    }
  }, [open]);

  // Debounce the query — 120ms matches the cadence of a fast-typing user.
  React.useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(query), DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [query]);

  // Fetch on every debounced query change (including empty — the empty
  // case primes the listing so the first keystroke is fast).
  React.useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let cancelled = false;
    setLoading(true);
    setError(null);

    getFileList(sessionId, {
      query: debounced || undefined,
      max: RESULT_CAP,
      signal: controller.signal,
    })
      .then((res: FileListResponse) => {
        if (cancelled) return;
        const mapped = res.paths.map((p) => ({ path: p, name: basename(p) }));
        setRows(mapped);
        setTruncated(res.truncated);
        setActive(0);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        setRows([]);
        setTruncated(false);
        setError(
          err instanceof ApiError
            ? err.message
            : "Could not load workspace files.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [debounced, open, sessionId]);

  // Keep the highlighted row scrolled into view as the user arrows down.
  React.useEffect(() => {
    if (!listRef.current) return;
    const el = listRef.current.querySelector<HTMLElement>(
      `[data-index="${active}"]`,
    );
    if (el) {
      el.scrollIntoView({ block: "nearest" });
    }
  }, [active]);

  const handleKey = React.useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (rows.length === 0) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActive((i) => (i + 1) % rows.length);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive((i) => (i - 1 + rows.length) % rows.length);
      } else if (event.key === "Enter") {
        event.preventDefault();
        const row = rows[active];
        if (row) {
          onSelect(row.path);
          onOpenChange(false);
        }
      }
    },
    [active, onOpenChange, onSelect, rows],
  );

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          aria-label="Quick open file"
          onKeyDown={handleKey}
          className={cn(
            "fixed left-1/2 top-[16%] z-50 w-[min(90vw,640px)] -translate-x-1/2",
            "rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-elevated",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
            "data-[state=open]:zoom-in-95 data-[state=closed]:zoom-out-95",
          )}
        >
          <DialogPrimitive.Title className="sr-only">
            Quick open file
          </DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">
            Type to filter workspace files. Use arrow keys to move; press
            Enter to open a file. Press Escape to dismiss.
          </DialogPrimitive.Description>
          <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-3 py-2">
            <SearchIcon
              className="size-4 text-[var(--color-muted-foreground)]"
              aria-hidden
            />
            <input
              autoFocus
              type="text"
              role="combobox"
              aria-expanded
              aria-controls="command-palette-listbox"
              aria-activedescendant={
                rows[active] ? `cmdp-row-${active}` : undefined
              }
              placeholder="Find file…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="w-full bg-transparent font-mono text-sm outline-none placeholder:text-[var(--color-muted-foreground)]"
              data-testid="command-palette-input"
            />
          </div>
          <ul
            ref={listRef}
            id="command-palette-listbox"
            role="listbox"
            aria-label="Workspace files"
            className="max-h-72 overflow-auto py-1"
          >
            {loading && rows.length === 0 ? (
              <li className="px-3 py-2 font-mono text-xs text-[var(--color-muted-foreground)]">
                {"// loading…"}
              </li>
            ) : null}
            {!loading && rows.length === 0 && !error ? (
              <li className="px-3 py-2 font-mono text-xs text-[var(--color-muted-foreground)]">
                {debounced
                  ? "// no files match that query"
                  : "// type to filter..."}
              </li>
            ) : null}
            {error ? (
              <li
                role="alert"
                className="px-3 py-2 font-mono text-xs text-[var(--color-danger)]"
              >
                {error}
              </li>
            ) : null}
            {rows.map((row, idx) => {
              const isActive = idx === active;
              return (
                <li
                  key={row.path}
                  id={`cmdp-row-${idx}`}
                  role="option"
                  aria-selected={isActive}
                  data-index={idx}
                  data-testid="command-palette-row"
                  onMouseEnter={() => setActive(idx)}
                  onClick={() => {
                    onSelect(row.path);
                    onOpenChange(false);
                  }}
                  className={cn(
                    "flex cursor-pointer items-center gap-2 px-3 py-1.5 font-mono text-xs",
                    isActive
                      ? "bg-[var(--color-muted)] text-[var(--color-foreground)]"
                      : "text-[var(--color-foreground)] hover:bg-[var(--color-muted)]/70",
                  )}
                >
                  <FileIcon
                    className="size-3.5 shrink-0 text-[var(--color-muted-foreground)]"
                    aria-hidden
                  />
                  <span className="truncate">{row.name}</span>
                  <span className="ml-auto truncate text-[10px] text-[var(--color-muted-foreground)]">
                    {row.path}
                  </span>
                </li>
              );
            })}
          </ul>
          {truncated ? (
            <div
              data-testid="command-palette-truncated"
              className="border-t border-[var(--color-border)] px-3 py-1 font-mono text-[10px] uppercase tracking-wide text-[var(--color-warning)]"
            >
              {"// some files omitted — refine your query"}
            </div>
          ) : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

function basename(path: string): string {
  const idx = path.lastIndexOf("/");
  return idx === -1 ? path : path.slice(idx + 1);
}
