"use client";

import * as React from "react";
import {
  Decoration,
  Diff,
  Hunk,
  parseDiff,
  type ChangeData,
  type HunkData,
  type ViewType,
} from "react-diff-view";
import gitDiffParser from "gitdiff-parser";
import { Columns2, FileDiff, Map as MapIcon, Rows3 } from "lucide-react";
import "react-diff-view/style/index.css";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

type DiffMode = ViewType | "minimap";

export interface DiffViewerProps {
  /** Raw unified diff (output of `git diff` or `GET /sessions/{id}/diff`). */
  unifiedDiff: string;
  /** Default render mode. */
  defaultViewType?: ViewType;
  className?: string;
  /**
   * Fired once when the user first views a non-empty diff. The workspace
   * passes a thunk that POSTs `/sessions/{id}/events/diff-opened` — the
   * resulting `diff.opened` supervision event drives the "Agent Output
   * Review" score dimension. The argument is the path the user is viewing
   * (`""` for the aggregate workspace-wide surface).
   */
  onDiffOpened?: (path: string) => void;
  /**
   * Path currently in focus when the diff opens — `""` (default) means the
   * workspace-wide aggregate surface; otherwise a single file path. Wired
   * to the locked `{path: string}` POST body.
   */
  activePath?: string;
}

// react-diff-view returns structurally-typed file objects; in practice the
// `type` discriminator varies across releases so we model the fields we
// touch and keep hunks loose to stay forward-compatible.
type ParsedDiffFile = ReturnType<typeof parseDiff>[number] & {
  oldPath?: string;
  newPath?: string;
};

function countChanges(file: ParsedDiffFile): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  const hunks = (file.hunks ?? []) as HunkData[];
  for (const h of hunks) {
    for (const change of h.changes as ChangeData[]) {
      if (change.type === "insert") added += 1;
      else if (change.type === "delete") removed += 1;
    }
  }
  return { added, removed };
}

/**
 * Side-by-side / inline / minimap diff renderer for the workspace.
 *
 * - Per-file `+N / -M` counts in the sticky header.
 * - Gutter glyphs (▲ / ▼) so the change kind reads without colour vision.
 * - A third "minimap" mode showing a vertical overview ruler — useful when a
 *   patch spans many files and you want to scan it at a glance.
 *
 * Falls back to gitdiff-parser when react-diff-view's bundled parser misses
 * something — keeps us resilient to weird whitespace from `git apply --3way`.
 */
export function DiffViewer({
  unifiedDiff,
  defaultViewType = "split",
  className,
  onDiffOpened,
  activePath = "",
}: DiffViewerProps) {
  const [mode, setMode] = React.useState<DiffMode>(defaultViewType);

  const files = React.useMemo<ParsedDiffFile[]>(() => {
    if (!unifiedDiff.trim()) return [];
    try {
      const primary = parseDiff(unifiedDiff);
      if (primary.length > 0) return primary;
    } catch {
      /* fall through */
    }
    try {
      // gitdiff-parser's published `.d.ts` lags react-diff-view's expectations —
      // the returned hunks are structurally identical (same `changes`/`content`
      // shape), so the double-cast is safe for our downstream readers.
      return gitDiffParser.parse(unifiedDiff) as unknown as ParsedDiffFile[];
    } catch {
      return [];
    }
  }, [unifiedDiff]);

  // Track which paths we've already emitted `diff.opened` for so that
  // switching between files in the same session each fires exactly once,
  // rather than only the very first one (the previous behaviour, a global
  // boolean, masked drift between per-file scoring expectations and what
  // the workspace actually emitted).
  const firedPathsRef = React.useRef<Set<string>>(new Set());
  React.useEffect(() => {
    if (!onDiffOpened) return;
    if (files.length === 0) return;
    if (firedPathsRef.current.has(activePath)) return;
    firedPathsRef.current.add(activePath);
    onDiffOpened(activePath);
  }, [activePath, files.length, onDiffOpened]);

  if (files.length === 0) {
    return (
      <div
        className={cn(
          "flex h-full flex-col items-center justify-center gap-2 p-6 text-sm text-[var(--color-muted-foreground)]",
          className
        )}
      >
        <FileDiff className="size-5" aria-hidden />
        <p>No changes yet.</p>
        <p className="text-xs">
          Apply an agent patch or edit a file to see a diff here.
        </p>
      </div>
    );
  }

  const totals = files.reduce(
    (acc, f) => {
      const c = countChanges(f);
      acc.added += c.added;
      acc.removed += c.removed;
      return acc;
    },
    { added: 0, removed: 0 }
  );

  return (
    <div className={cn("flex h-full flex-col", className)}>
      <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2">
        <p className="text-xs text-[var(--color-muted-foreground)]">
          {files.length} file{files.length === 1 ? "" : "s"} changed{" "}
          <span className="font-mono text-[var(--color-success)]">
            +{totals.added}
          </span>{" "}
          <span className="font-mono text-[var(--color-danger)]">
            −{totals.removed}
          </span>
        </p>
        <div
          className="flex items-center gap-1"
          role="radiogroup"
          aria-label="Diff view mode"
        >
          <Button
            size="sm"
            variant={mode === "split" ? "secondary" : "ghost"}
            onClick={() => setMode("split")}
            role="radio"
            aria-checked={mode === "split"}
          >
            <Columns2 className="size-3.5" aria-hidden />
            Side-by-side
          </Button>
          <Button
            size="sm"
            variant={mode === "unified" ? "secondary" : "ghost"}
            onClick={() => setMode("unified")}
            role="radio"
            aria-checked={mode === "unified"}
          >
            <Rows3 className="size-3.5" aria-hidden />
            Inline
          </Button>
          <Button
            size="sm"
            variant={mode === "minimap" ? "secondary" : "ghost"}
            onClick={() => setMode("minimap")}
            role="radio"
            aria-checked={mode === "minimap"}
          >
            <MapIcon className="size-3.5" aria-hidden />
            Minimap
          </Button>
        </div>
      </div>
      <div className="flex-1 overflow-auto bg-[var(--color-surface)]">
        {mode === "minimap" ? (
          <DiffMinimap files={files} />
        ) : (
          files.map((file) => {
            const { added, removed } = countChanges(file);
            return (
              <article
                key={`${file.oldPath ?? "a"}|${file.newPath ?? "b"}|${file.type}`}
                className="border-b border-[var(--color-border)] last:border-b-0"
              >
                <header className="sticky top-0 z-10 flex items-center justify-between gap-2 border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3 py-1.5 font-mono text-[11px]">
                  <span className="flex min-w-0 items-center gap-2">
                    <FileDiff
                      className="size-3.5 text-[var(--color-muted-foreground)]"
                      aria-hidden
                    />
                    <span className="truncate">
                      {file.newPath ?? file.oldPath ?? "(unknown)"}
                    </span>
                  </span>
                  <span
                    className="shrink-0 text-[var(--color-muted-foreground)]"
                    aria-label={`${added} added, ${removed} removed`}
                    data-testid="diff-file-count"
                  >
                    <span className="font-mono text-[var(--color-success)]">
                      +{added}
                    </span>
                    {" "}
                    <span className="font-mono text-[var(--color-danger)]">
                      −{removed}
                    </span>
                  </span>
                </header>
                <Diff
                  viewType={mode}
                  diffType={file.type}
                  hunks={file.hunks ?? []}
                  className="text-[12px]"
                  gutterType="anchor"
                  generateAnchorID={(change: ChangeData) =>
                    `diff-${file.newPath ?? file.oldPath}-${changeAnchor(change)}`
                  }
                  renderGutter={({ change, renderDefault }) => (
                    <>
                      <span
                        aria-hidden
                        className={cn(
                          "mr-1 font-mono text-[11px] tabular-nums",
                          change.type === "insert" && "text-[var(--color-success)]",
                          change.type === "delete" && "text-[var(--color-danger)]"
                        )}
                      >
                        {change.type === "insert"
                          ? "+"
                          : change.type === "delete"
                            ? "−"
                            : " "}
                      </span>
                      {renderDefault()}
                    </>
                  )}
                >
                  {(hunks) =>
                    (hunks as HunkData[]).flatMap((hunk) => [
                      <Decoration key={`d-${hunk.content}`}>
                        <span className="block bg-[var(--color-muted)] px-3 py-0.5 font-mono text-[10px] text-[var(--color-muted-foreground)]">
                          {hunk.content}
                        </span>
                      </Decoration>,
                      <Hunk key={hunk.content} hunk={hunk} />,
                    ])
                  }
                </Diff>
              </article>
            );
          })
        )}
      </div>
    </div>
  );
}

function changeAnchor(change: ChangeData): string {
  if (change.type === "insert") return `i${change.lineNumber}`;
  if (change.type === "delete") return `d${change.lineNumber}`;
  return `n${change.newLineNumber ?? change.oldLineNumber ?? 0}`;
}

interface DiffMinimapProps {
  files: ParsedDiffFile[];
}

/**
 * Compact "overview ruler" — every file becomes a vertical bar with a
 * per-line stripe (green = add, red = remove). Useful for spotting unrelated
 * scope drift without scrolling the full diff.
 */
function DiffMinimap({ files }: DiffMinimapProps) {
  return (
    <ul className="grid grid-cols-1 gap-3 p-3 sm:grid-cols-2">
      {files.map((file) => {
        const hunks = (file.hunks ?? []) as HunkData[];
        const stripes: { type: "insert" | "delete" | "normal" }[] = [];
        for (const h of hunks) {
          for (const c of h.changes as ChangeData[]) stripes.push({ type: c.type });
        }
        const { added, removed } = countChanges(file);
        return (
          <li
            key={`${file.oldPath ?? "a"}|${file.newPath ?? "b"}|${file.type}`}
            className="flex items-stretch gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-3"
          >
            <div
              aria-hidden
              className="flex w-2 flex-col overflow-hidden rounded"
              data-testid="diff-minimap-ruler"
            >
              {stripes.map((s, i) => (
                <span
                  key={i}
                  className={cn(
                    "block flex-1",
                    s.type === "insert" &&
                      "bg-[oklch(from_var(--color-success)_l_c_h/0.7)]",
                    s.type === "delete" &&
                      "bg-[oklch(from_var(--color-danger)_l_c_h/0.7)]",
                    s.type === "normal" && "bg-[var(--color-border)]"
                  )}
                  style={{ minHeight: 2 }}
                />
              ))}
            </div>
            <div className="flex flex-1 flex-col justify-between">
              <p className="truncate font-mono text-[11px]">
                {file.newPath ?? file.oldPath ?? "(unknown)"}
              </p>
              <p className="font-mono text-[10px] text-[var(--color-muted-foreground)]">
                <span className="text-[var(--color-success)]">+{added}</span>
                {" "}
                <span className="text-[var(--color-danger)]">−{removed}</span>{" "}
                · {hunks.length} hunk{hunks.length === 1 ? "" : "s"}
              </p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
