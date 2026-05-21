"use client";

import * as React from "react";
import dynamic from "next/dynamic";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RotateCcw } from "lucide-react";
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

  React.useEffect(
    () => () => {
      if (saveTimerRef.current !== undefined) clearTimeout(saveTimerRef.current);
    },
    []
  );

  function scheduleWrite(targetPath: string, value: string) {
    setSavingState("saving");
    if (saveTimerRef.current !== undefined) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      void writeFile(sessionId, targetPath, value)
        .then(() => {
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
    const value = next ?? "";
    setActiveFileContent(path, value);
    scheduleWrite(path, value);
  }

  async function handleRevert() {
    if (!path) return;
    try {
      await revertFile(sessionId, path);
      // Drop the cached buffer so the next fetch re-seeds.
      setActiveFileContent(path, "");
      queryClient.invalidateQueries({ queryKey: ["file", sessionId, path] });
      queryClient.invalidateQueries({ queryKey: ["session", sessionId, "diff"] });
      const refreshed = await getFile(sessionId, path);
      setActiveFileContent(path, refreshed.content);
      toast.success(`Reverted ${path}.`);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Failed to revert."
      );
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
          <div className="flex h-full items-center justify-center text-xs text-[var(--color-danger)]">
            Couldn&rsquo;t load this file.
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
  if (loading) return null;
  if (saving) {
    return (
      <span
        className="inline-flex items-center gap-1 text-[var(--color-muted-foreground)]"
        role="status"
      >
        <Loader2 className="size-3 animate-spin" aria-hidden /> saving…
      </span>
    );
  }
  if (saved) {
    return (
      <span
        className="text-[var(--color-success)]"
        role="status"
        aria-live="polite"
      >
        saved
      </span>
    );
  }
  return null;
}
