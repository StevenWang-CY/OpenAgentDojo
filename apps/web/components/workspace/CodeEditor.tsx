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
    // Cancel any pending debounced write so it can't fire mid-revert with
    // stale content.
    if (saveTimerRef.current !== undefined) {
      clearTimeout(saveTimerRef.current);
      saveTimerRef.current = undefined;
    }
    setSavingState("idle");
    try {
      await revertFile(sessionId, revertPath);
      // Cancel any in-flight `file` query so a stale resolution can't race
      // the refetch we're about to issue. Then invalidate + refetch via
      // React Query (no manual `getFile` — single source of truth).
      await queryClient.cancelQueries({
        queryKey: ["file", sessionId, revertPath],
      });
      await queryClient.invalidateQueries({
        queryKey: ["session", sessionId, "diff"],
      });
      const refreshed = await queryClient.fetchQuery({
        queryKey: ["file", sessionId, revertPath],
        queryFn: ({ signal }) => getFile(sessionId, revertPath, signal),
      });
      setActiveFileContent(revertPath, refreshed.content);
      toast.success(`Reverted ${revertPath}.`);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Failed to revert."
      );
    } finally {
      revertingRef.current = false;
    }
  }

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
