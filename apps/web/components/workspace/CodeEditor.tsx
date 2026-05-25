"use client";

import * as React from "react";
import dynamic from "next/dynamic";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Loader2, RefreshCcw, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { ApiError, getFile, revertFile, writeFile } from "@/lib/api";
import { useWorkspaceStore } from "@/stores/workspaceStore";
import { useTheme } from "@/stores/themeStore";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

const MonacoEditor = dynamic(
  () => import("@monaco-editor/react").then((m) => m.default),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full items-center justify-center text-xs text-[var(--color-muted-foreground)]">
        <Loader2 className="mr-2 size-3.5 animate-spin" aria-hidden /> Loading editor…
      </div>
    ),
  }
);

export interface CodeEditorProps {
  /** Required so we can hit `/sessions/{id}/file?path=…` and the write endpoint. */
  sessionId: string;
  /** Active file path — null means "no file open". */
  path: string | null;
  readOnly?: boolean;
  className?: string;
}

const LANGUAGE_BY_EXT: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  py: "python",
  json: "json",
  md: "markdown",
  yml: "yaml",
  yaml: "yaml",
  css: "css",
  scss: "scss",
  html: "html",
  sh: "shell",
  toml: "ini",
  sql: "sql",
};

function languageFor(path: string | null): string {
  if (!path) return "plaintext";
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return LANGUAGE_BY_EXT[ext] ?? "plaintext";
}

const DEBOUNCE_MS = 300;

/**
 * Monaco-backed file editor. Fetches the file's content on open via
 * `GET /sessions/{id}/file`, seeds the editor, and writes back via a
 * 300ms-debounced `POST /sessions/{id}/files`. A per-tab Revert button
 * resets the file to the last commit and re-fetches.
 */
export function CodeEditor({
  sessionId,
  path,
  readOnly = false,
  className,
}: CodeEditorProps) {
  const { resolvedTheme } = useTheme();
  const queryClient = useQueryClient();
  const store = useWorkspaceStore(sessionId);
  const fileBuffers = store((s) => s.fileBuffers);
  const setActiveFileContent = store((s) => s.setActiveFileContent);
  // P0-9 — find-in-files drives the editor to a specific line via
  // ``workspaceStore.activeLineFocus``. We read the value, reveal it once
  // the editor has rendered, then clear it from the store so a later
  // open of the same file doesn't keep stealing focus.
  const activeLineFocus = store((s) => s.activeLineFocus);
  const setActiveLineFocus = store((s) => s.setActiveLineFocus);
  // ``editor.IStandaloneCodeEditor`` from Monaco — typed as ``any`` because
  // we don't bundle ``monaco-editor`` types in this component and the
  // surface we touch is tiny (``revealLineInCenter`` + ``setPosition``).
  const editorRef = React.useRef<unknown>(null);

  const fileQuery = useQuery({
    queryKey: ["file", sessionId, path],
    queryFn: ({ signal }) => getFile(sessionId, path as string, signal),
    enabled: !!path,
    retry: (failureCount, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return failureCount < 1;
    },
  });

  // Seed the local buffer once the file loads (if the user hasn't already
  // typed into it). We keep the *buffer* as the source of truth so the user
  // doesn't lose unsaved typing on a refetch.
  React.useEffect(() => {
    if (!path) return;
    if (!fileQuery.data) return;
    if (fileBuffers[path] !== undefined) return;
    setActiveFileContent(path, fileQuery.data.content);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, fileQuery.data]);

  // P0-9 — honour a pending line-focus when the file content lands. Runs
  // after the editor is already mounted (the onMount handler covers the
  // first-mount case) so quick-open + find-in-files flows both reveal the
  // requested line.
  React.useEffect(() => {
    if (!activeLineFocus || activeLineFocus <= 0) return;
    if (!path) return;
    if (!fileQuery.data) return;
    const editor = editorRef.current;
    if (!editor) return;
    try {
      const ed = editor as {
        revealLineInCenter: (line: number) => void;
        setPosition: (pos: { lineNumber: number; column: number }) => void;
        focus: () => void;
      };
      ed.revealLineInCenter(activeLineFocus);
      ed.setPosition({ lineNumber: activeLineFocus, column: 1 });
      ed.focus();
    } catch {
      // ignore — best-effort focus
    }
    setActiveLineFocus(null);
  }, [activeLineFocus, path, fileQuery.data, setActiveLineFocus]);

  const [savingState, setSavingState] = React.useState<"idle" | "saving" | "saved">(
    "idle"
  );
  const saveTimerRef = React.useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined
  );
  // While `true`, any pending debounced `writeFile` must be cancelled and any
  // editor-change events ignored — otherwise the revert's `setActiveFileContent`
  // races with a tail-end write of the *pre-revert* content and resurrects the
  // user's edits.
  const revertingRef = React.useRef(false);
  // FE-P2 audit fix — monotonic counter bumped at the start of every revert
  // so the post-await invalidate/refetch can detect "another revert started
  // while I was mid-flight" and skip its own invalidation. Same epoch is
  // checked by any inbound WS-driven invalidation hook the parent might
  // hand us in the future, so a late `file.updated` event can't overwrite
  // the freshly-reverted buffer.
  const revertEpochRef = React.useRef(0);
  const lastRevertAtRef = React.useRef(0);

  React.useEffect(
    () => () => {
      if (saveTimerRef.current !== undefined) clearTimeout(saveTimerRef.current);
    },
    []
  );

  function scheduleWrite(targetPath: string, value: string) {
    // A revert is in progress — any value we'd schedule here is by
    // definition stale, so skip it entirely.
    if (revertingRef.current) return;
    setSavingState("saving");
    if (saveTimerRef.current !== undefined) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      // Re-check on fire as well — the revert might have started during
      // the debounce window after we scheduled.
      if (revertingRef.current) return;
      void writeFile(sessionId, targetPath, value)
        .then(() => {
          if (revertingRef.current) return;
          setSavingState("saved");
          queryClient.invalidateQueries({
            queryKey: ["session", sessionId, "diff"],
          });
        })
        .catch((err) => {
          setSavingState("idle");
          toast.error(
            err instanceof ApiError ? err.message : "Failed to save."
          );
        });
    }, DEBOUNCE_MS);
  }

  function handleEditorChange(next: string | undefined) {
    if (!path) return;
    if (revertingRef.current) return;
    const value = next ?? "";
    setActiveFileContent(path, value);
    scheduleWrite(path, value);
  }

  async function handleRevert() {
    if (!path) return;
    const revertPath = path;
    revertingRef.current = true;
    // Snapshot the epoch *before* awaiting anything so a second revert
    // (or a late WS-driven file invalidation) can win the latest-revert
    // race deterministically.
    const epoch = ++revertEpochRef.current;
    lastRevertAtRef.current = Date.now();
    // Cancel any pending debounced write so it can't fire mid-revert with
    // stale content.
    if (saveTimerRef.current !== undefined) {
      clearTimeout(saveTimerRef.current);
      saveTimerRef.current = undefined;
    }
    setSavingState("idle");
    try {
      await revertFile(sessionId, revertPath);
      // Bail out if a newer revert started while we were awaiting — its
      // own post-await flow will own the invalidate + refetch chain.
      if (epoch !== revertEpochRef.current) return;
      // Cancel any in-flight `file` query so a stale resolution can't race
      // the refetch we're about to issue. Then invalidate + refetch via
      // React Query (no manual `getFile` — single source of truth).
      await queryClient.cancelQueries({
        queryKey: ["file", sessionId, revertPath],
      });
      if (epoch !== revertEpochRef.current) return;
      await queryClient.invalidateQueries({
        queryKey: ["session", sessionId, "diff"],
      });
      if (epoch !== revertEpochRef.current) return;
      const refreshed = await queryClient.fetchQuery({
        queryKey: ["file", sessionId, revertPath],
        queryFn: ({ signal }) => getFile(sessionId, revertPath, signal),
      });
      if (epoch !== revertEpochRef.current) return;
      setActiveFileContent(revertPath, refreshed.content);
      toast.success(`Reverted ${revertPath}.`);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Failed to revert."
      );
    } finally {
      // Only the latest revert is allowed to flip the flag off — earlier
      // attempts must leave it set so the newer one's gating still works.
      if (epoch === revertEpochRef.current) {
        revertingRef.current = false;
      }
    }
  }

  // FE-P2 audit fix — `revertingRef`, `revertEpochRef` and `lastRevertAtRef`
  // collectively gate any WS-driven invalidation of this file's query: the
  // parent shell consults `revertingRef` (in-flight) or `lastRevertAtRef`
  // (recent enough to be the echo of our own revert) before forcing a
  // refetch via `file.updated` events. Centralised here so a future event
  // handler doesn't have to re-implement the policy.

  if (!path) {
    return (
      <div
        className={cn(
          "flex h-full items-center justify-center text-sm text-[var(--color-muted-foreground)]",
          className
        )}
      >
        Select a file from the tree to start editing.
      </div>
    );
  }

  const buffered = fileBuffers[path];
  const value = buffered ?? fileQuery.data?.content ?? "";

  return (
    <div className={cn("flex h-full flex-col", className)}>
      <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3 py-1">
        <p className="truncate font-mono text-[11px] text-[var(--color-muted-foreground)]">
          {path}
        </p>
        <div className="flex items-center gap-2 text-[11px]">
          <SaveBadge
            saving={savingState === "saving"}
            saved={savingState === "saved"}
            loading={fileQuery.isLoading}
          />
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void handleRevert()}
            aria-label={`Revert ${path}`}
          >
            <RotateCcw className="size-3.5" aria-hidden />
            Revert
          </Button>
        </div>
      </div>
      <div className="flex-1">
        {fileQuery.isLoading ? (
          <div className="flex h-full items-center justify-center text-xs text-[var(--color-muted-foreground)]">
            <Loader2 className="mr-2 size-3.5 animate-spin" aria-hidden /> Loading file…
          </div>
        ) : fileQuery.error ? (
          <div
            className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center"
            role="alert"
            data-testid="code-editor-error"
          >
            <AlertCircle
              className="size-5 text-[var(--color-danger)]"
              aria-hidden
            />
            <p className="text-xs text-[var(--color-danger)]">
              Couldn&rsquo;t load <span className="font-mono">{path}</span>
            </p>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => void fileQuery.refetch()}
            >
              <RefreshCcw className="size-3.5" aria-hidden />
              Retry
            </Button>
          </div>
        ) : (
          <MonacoEditor
            path={path}
            language={languageFor(path)}
            value={value}
            onChange={handleEditorChange}
            onMount={(editor) => {
              editorRef.current = editor;
              // If a search-driven line focus was already queued before the
              // editor mounted, honour it now and clear the store flag.
              if (activeLineFocus && activeLineFocus > 0) {
                try {
                  const ed = editor as {
                    revealLineInCenter: (line: number) => void;
                    setPosition: (pos: { lineNumber: number; column: number }) => void;
                    focus: () => void;
                  };
                  ed.revealLineInCenter(activeLineFocus);
                  ed.setPosition({ lineNumber: activeLineFocus, column: 1 });
                  ed.focus();
                } catch {
                  // ignore — best-effort focus, never block the editor mount
                }
                setActiveLineFocus(null);
              }
            }}
            theme={resolvedTheme === "dark" ? "vs-dark" : "light"}
            options={{
              fontFamily:
                '"SF Mono", "JetBrains Mono", Menlo, Monaco, Consolas, "Liberation Mono", monospace',
              fontSize: 13,
              lineHeight: 20,
              minimap: { enabled: false },
              smoothScrolling: true,
              scrollBeyondLastLine: false,
              cursorBlinking: "smooth",
              cursorSmoothCaretAnimation: "on",
              renderWhitespace: "selection",
              tabSize: 2,
              padding: { top: 12, bottom: 12 },
              automaticLayout: true,
              readOnly,
              accessibilitySupport: "on",
              ariaLabel: `Editor: ${path}`,
            }}
          />
        )}
      </div>
    </div>
  );
}

function SaveBadge({
  saving,
  saved,
  loading,
}: {
  saving: boolean;
  saved: boolean;
  loading: boolean;
}) {
  // Always render a stable layout slot so the header doesn't reflow between
  // load → saving → saved transitions (avoids CLS in the toolbar row).
  const baseClass =
    "inline-flex min-w-[58px] items-center justify-end gap-1 tabular-nums";
  if (loading) {
    return (
      <span
        className={cn(baseClass, "text-[var(--color-muted-foreground)]")}
        role="status"
        aria-live="polite"
      >
        <Loader2 className="size-3 animate-spin" aria-hidden /> loading…
      </span>
    );
  }
  if (saving) {
    return (
      <span
        className={cn(baseClass, "text-[var(--color-muted-foreground)]")}
        role="status"
        aria-live="polite"
      >
        <Loader2 className="size-3 animate-spin" aria-hidden /> saving…
      </span>
    );
  }
  if (saved) {
    return (
      <span
        className={cn(baseClass, "text-[var(--color-success)]")}
        role="status"
        aria-live="polite"
      >
        saved
      </span>
    );
  }
  return <span className={baseClass} aria-hidden />;
}
