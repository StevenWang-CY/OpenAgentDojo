"use client";

import * as React from "react";
import { ChevronRight, File as FileIcon, Folder, FolderOpen } from "lucide-react";
import type { FileTreeNode } from "@arena/shared-types";
import { Checkbox } from "@/components/ui/Checkbox";
import { ScrollArea } from "@/components/ui/ScrollArea";
import { setContext } from "@/lib/api";
import { cn } from "@/lib/utils";

const CONTEXT_DEBOUNCE_MS = 500;

export interface FileTreeProps {
  /** Nested tree, as returned by `GET /sessions/{id}/tree`. */
  nodes: FileTreeNode[];
  /** Currently-open file path (highlighted). */
  activePath?: string | null;
  /** Paths selected for agent context (drives checkbox state). */
  selectedContext?: string[];
  /**
   * Session id. Required to push selection changes to
   * `POST /sessions/{id}/context`; omit to opt out of the debounced sync (e.g.
   * for static/server-rendered previews).
   */
  sessionId?: string;
  /** Open a file in the editor. */
  onOpenFile?(path: string): void;
  /** Toggle a path's inclusion in the agent's context. */
  onToggleContext?(path: string): void;
  className?: string;
}

interface FlatRow {
  node: FileTreeNode;
  depth: number;
}

function flatten(
  nodes: FileTreeNode[],
  expanded: Set<string>,
  depth = 0,
  acc: FlatRow[] = []
): FlatRow[] {
  for (const node of nodes) {
    acc.push({ node, depth });
    if (node.kind === "directory" && expanded.has(node.path) && node.children) {
      flatten(node.children, expanded, depth + 1, acc);
    }
  }
  return acc;
}

export function FileTree({
  nodes,
  activePath,
  selectedContext,
  sessionId,
  onOpenFile,
  onToggleContext,
  className,
}: FileTreeProps) {
  // Debounced POST to /context so rapid toggling collapses into one request.
  const debounceRef = React.useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined
  );
  const lastSyncedKey = React.useRef<string>("");
  const selectedKey = JSON.stringify(selectedContext ?? []);
  // Stash the latest selection so the unmount cleanup can read the freshest
  // value without forcing the cleanup-on-dep-change to also flush.
  const latestSelectedRef = React.useRef<string[]>(selectedContext ?? []);
  React.useEffect(() => {
    latestSelectedRef.current = selectedContext ?? [];
  }, [selectedContext]);
  const latestSessionIdRef = React.useRef<string | undefined>(sessionId);
  React.useEffect(() => {
    latestSessionIdRef.current = sessionId;
  }, [sessionId]);
  React.useEffect(() => {
    if (!sessionId) return;
    if (selectedKey === lastSyncedKey.current) return;
    if (debounceRef.current !== undefined) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      lastSyncedKey.current = selectedKey;
      void setContext(sessionId, {
        files: selectedContext ?? [],
        logs: [],
        tests: [],
        extras: [],
      }).catch(() => {
        // Best-effort — the shell shows a toast on hard errors elsewhere.
      });
    }, CONTEXT_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current !== undefined) {
        clearTimeout(debounceRef.current);
        debounceRef.current = undefined;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedKey, sessionId]);

  // Unmount-only flush: if the user toggled a path inside the debounce window
  // and then navigated away (e.g. the workspace teardown), we want to fire
  // one final POST so their intent isn't silently dropped. This effect has
  // an empty dep array so the cleanup runs only on unmount, not on every
  // selection change — that path is owned by the debounced effect above.
  React.useEffect(() => {
    // Capture the session id we mounted with so the unmount flush can
    // verify it still matches the live ref. If the parent has since
    // swapped to a different session id, firing /context for the old
    // session id would race against the new session's debounced sync and
    // poison its selection on the backend.
    const mountedSessionId = sessionId;
    return () => {
      const finalSelectedKey = JSON.stringify(latestSelectedRef.current);
      if (finalSelectedKey === lastSyncedKey.current) return;
      const sid = latestSessionIdRef.current;
      if (!sid) return;
      if (sid !== mountedSessionId) return;
      lastSyncedKey.current = finalSelectedKey;
      void setContext(sid, {
        files: latestSelectedRef.current,
        logs: [],
        tests: [],
        extras: [],
      }).catch(() => {
        /* best-effort — same as the debounced path */
      });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleToggleContext(path: string): void {
    onToggleContext?.(path);
  }
  // Default to expanding the top level so users immediately see structure.
  const [expanded, setExpanded] = React.useState<Set<string>>(() => {
    const initial = new Set<string>();
    for (const n of nodes) {
      if (n.kind === "directory") initial.add(n.path);
    }
    return initial;
  });

  const rows = React.useMemo(() => flatten(nodes, expanded), [nodes, expanded]);

  const selectedSet = React.useMemo(
    () => new Set(selectedContext ?? []),
    [selectedContext]
  );

  function toggleFolder(path: string): void {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  if (nodes.length === 0) {
    return (
      <div
        className={cn(
          "flex h-full items-center justify-center p-4 text-xs text-[var(--color-muted-foreground)]",
          className
        )}
      >
        No files in this workspace yet.
      </div>
    );
  }

  return (
    <ScrollArea className={cn("h-full", className)}>
      <ul
        role="tree"
        aria-label="Workspace files"
        className="py-1 text-sm"
      >
        {rows.map(({ node, depth }) => {
          const isDir = node.kind === "directory";
          const isOpen = expanded.has(node.path);
          const isActive = !isDir && node.path === activePath;
          const isSelected = selectedSet.has(node.path);
          return (
            <li
              key={node.path}
              role="treeitem"
              aria-expanded={isDir ? isOpen : undefined}
              aria-selected={isActive || undefined}
              className={cn(
                "group flex items-center gap-1 px-2 py-0.5 font-mono text-xs",
                "transition-colors duration-100",
                isActive
                  ? "bg-[oklch(from_var(--color-primary)_l_c_h/0.15)] text-[var(--color-primary)]"
                  : "text-[var(--color-foreground)] hover:bg-[var(--color-muted)]"
              )}
              style={{ paddingLeft: `${8 + depth * 12}px` }}
            >
              {onToggleContext ? (
                <Checkbox
                  checked={isSelected}
                  onCheckedChange={() => handleToggleContext(node.path)}
                  aria-label={`Add ${node.path} to agent context`}
                  className="size-3.5"
                  onClick={(e) => e.stopPropagation()}
                />
              ) : null}
              <button
                type="button"
                onClick={() => {
                  if (isDir) toggleFolder(node.path);
                  else onOpenFile?.(node.path);
                }}
                className="flex flex-1 items-center gap-1 truncate rounded-sm text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
              >
                {isDir ? (
                  <>
                    <ChevronRight
                      className={cn(
                        "size-3 shrink-0 transition-transform duration-150 ease-macos",
                        isOpen && "rotate-90"
                      )}
                      aria-hidden
                    />
                    {isOpen ? (
                      <FolderOpen className="size-3.5 shrink-0 text-[var(--color-accent)]" aria-hidden />
                    ) : (
                      <Folder className="size-3.5 shrink-0 text-[var(--color-accent)]" aria-hidden />
                    )}
                  </>
                ) : (
                  <>
                    <span className="size-3 shrink-0" aria-hidden />
                    <FileIcon className="size-3.5 shrink-0 text-[var(--color-muted-foreground)]" aria-hidden />
                  </>
                )}
                <span className="truncate">{node.name}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </ScrollArea>
  );
}
