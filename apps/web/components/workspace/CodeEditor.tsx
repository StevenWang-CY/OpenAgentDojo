"use client";

import * as React from "react";
import dynamic from "next/dynamic";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Loader2, RefreshCcw, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { ApiError, getFile, getWsToken, revertFile, writeFile } from "@/lib/api";
import { useWorkspaceStore } from "@/stores/workspaceStore";
import { useTheme } from "@/stores/themeStore";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import {
  createLSPManager,
  lspLanguageForMonaco,
  type LSPManager,
  type ManagedLSPClient,
} from "@/lib/lsp/manager";
import type { LSPLanguage, LSPState } from "@/lib/lsp/client";
import {
  completionsToMonaco,
  diagnosticsToMarkers,
  hoverToMonaco,
} from "@/lib/lsp/diagnostics";
import { trackLspEvent } from "@/lib/telemetry";

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

/**
 * Map an in-app file path (relative or otherwise) to a URI the LSP
 * recognises. The sandbox mounts the workspace at ``/workspace`` so we
 * normalise to ``file:///workspace/<path>`` — without a leading slash on
 * the relative side because LSPs canonicalise URIs themselves.
 */
function uriForPath(path: string): string {
  const stripped = path.replace(/^\/+/, "");
  return `file:///workspace/${stripped}`;
}

/** UI-side projection of the LSP client state for the footer chip. */
interface LSPChipState {
  visible: boolean;
  kind: "connecting" | "ready" | "error" | "disconnected";
  language: LSPLanguage | null;
  error: string | null;
}

function stateToChip(state: LSPState): LSPChipState {
  switch (state.kind) {
    case "connecting":
      return {
        visible: true,
        kind: "connecting",
        language: state.language,
        error: null,
      };
    case "ready":
      return {
        visible: true,
        kind: "ready",
        language: state.language,
        error: null,
      };
    case "error":
      return {
        visible: true,
        kind: "error",
        language: state.language,
        error: state.error,
      };
    case "disconnected":
      return {
        visible: true,
        kind: "disconnected",
        language: state.language,
        error: `close_${state.closeCode ?? "unknown"}`,
      };
  }
}

const LANGUAGE_BY_EXT: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  py: "python",
  // P1-3 — ``go`` joins the supported LSP set; the backend driver
  // resolves it to ``gopls``. Monaco recognises ``go`` natively.
  go: "go",
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
  const monacoRef = React.useRef<unknown>(null);

  // P1-3 — LSP manager. One per editor mount (i.e. one per session in
  // the workspace shell). Lives across file switches so a second file
  // in the same language reuses the open WS instead of re-handshaking.
  const lspManagerRef = React.useRef<LSPManager | null>(null);
  const lspProvidersDisposedRef = React.useRef<(() => void)[]>([]);
  const [lspChip, setLspChip] = React.useState<LSPChipState>({
    visible: false,
    kind: "connecting",
    language: null,
    error: null,
  });

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

  // P1-3 — Active LSP client for the currently-open file. The wiring
  // effect below populates this when the file's language is one of
  // python|typescript|go; ``handleEditorChange`` fires ``didChange`` via
  // this ref so we don't have to re-run the effect on every keystroke.
  const activeLspRef = React.useRef<ManagedLSPClient | null>(null);
  const docVersionRef = React.useRef(1);
  // P1 — one-shot guard for the Monaco private-API warning. The
  // suggest-controller resolution path uses ``getContribution`` first
  // and falls back to ``_contributions[...]``; if both return
  // ``undefined`` (Monaco renamed the controller in a future bump),
  // we emit a single dev console.warn so the regression is visible
  // without spamming the console on every Tab/Enter.
  const monacoContribWarnedRef = React.useRef(false);
  // Maps Monaco language id → providers registered with Monaco so we
  // never double-register. Keys are LSP language ids; values are the
  // disposer for the provider registration.
  const monacoProvidersRef = React.useRef<Map<LSPLanguage, () => void>>(
    new Map()
  );

  // Spin up the per-session LSP manager once per editor mount.
  // The manager owns the WS connections and the LRU cap; this component
  // only consumes its ``acquire`` API. Disposed on unmount so a workspace
  // tab close tears down every language server.
  React.useEffect(() => {
    const mgr = createLSPManager({
      sessionId,
      fetchToken: () => getWsToken(sessionId).then((r) => r.token),
      onStateChange: () => {
        // Only surface the chip for the file that's currently open —
        // the wiring effect below sets ``lspChip`` based on the active
        // language. The manager-level callback is a future hook for
        // background-language telemetry; today it's intentionally a
        // no-op so the chip wiring stays in one place.
      },
    });
    lspManagerRef.current = mgr;
    return () => {
      mgr.dispose();
      for (const dispose of lspProvidersDisposedRef.current) {
        try {
          dispose();
        } catch {
          // ignore
        }
      }
      lspProvidersDisposedRef.current = [];
    };
  }, [sessionId]);

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
    // P1-3 — push the unsaved buffer to the language server so hover,
    // completion, and diagnostics use the live text. The LSP picks up
    // saved files via its own watcher; this just keeps in-memory state
    // synchronised before save.
    const lsp = activeLspRef.current;
    if (lsp) {
      const nextVersion = ++docVersionRef.current;
      try {
        lsp.changeDocument(uriForPath(path), value, nextVersion);
      } catch {
        // ignore — best-effort; the next save will resync
      }
    }
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

  // P1-3 — wire the LSP for the active file. Runs when the file path,
  // its loaded content, or the manager ref change. The effect resolves:
  //   1. Detect the LSP language for the file (null → nothing to do).
  //   2. Acquire (or reuse) a client for that language from the manager.
  //   3. Fire ``textDocument/didOpen`` with the current buffer.
  //   4. Register a Monaco completion + hover provider for the language
  //      (once per language, not per file).
  //   5. Mirror ``publishDiagnostics`` notifications into Monaco markers.
  //
  // Closes the doc on cleanup so the LSP frees its file-scoped state;
  // the WS client itself stays open (it's owned by the manager LRU).
  React.useEffect(() => {
    if (!path) {
      activeLspRef.current = null;
      setLspChip({ visible: false, kind: "connecting", language: null, error: null });
      return;
    }
    const content = fileBuffers[path] ?? fileQuery.data?.content;
    if (content === undefined) return;

    const monacoLang = languageFor(path);
    const lspLang = lspLanguageForMonaco(monacoLang);
    if (!lspLang) {
      activeLspRef.current = null;
      setLspChip({ visible: false, kind: "connecting", language: null, error: null });
      return;
    }
    const manager = lspManagerRef.current;
    if (!manager) return;

    let disposed = false;
    let unsubscribeDiagnostics: (() => void) | null = null;
    let acquired: ManagedLSPClient | null = null;
    const fileUri = uriForPath(path);
    docVersionRef.current = 1;

    setLspChip({
      visible: true,
      kind: "connecting",
      language: lspLang,
      error: null,
    });

    // P1 — event-driven LSP chip. We subscribe to state transitions via
    // the manager's per-acquire ``onStateChange`` so the chip flips the
    // moment the underlying client's state machine reports a change,
    // instead of the previous setTimeout(pollTick, 250) loop. The
    // subscriber is detached in the effect cleanup below.
    const onLspState = (state: LSPState): void => {
      if (disposed) return;
      setLspChip(stateToChip(state));
    };
    let unsubscribeFromManager: (() => void) | null = null;

    void (async () => {
      const client = await manager.acquire(lspLang, {
        onStateChange: onLspState,
      });
      if (disposed) {
        // Race: a fast file switch tore the effect down before
        // acquire resolved. Drop the subscriber so we don't leak a
        // listener pointed at a stale setLspChip closure.
        manager.unsubscribe(lspLang, onLspState);
        return;
      }
      // Schedule the detach for cleanup. ``acquire`` may have
      // synchronously fired ``onLspState`` with the initial state
      // already; the manager seeds it so the chip is correct from
      // the first tick.
      unsubscribeFromManager = () => manager.unsubscribe(lspLang, onLspState);
      if (!client) {
        // FE-P4 audit fix — ``manager.acquire`` returns null when the
        // ws-token fetch fails (or the manager is disposed). The
        // manager already fired ``onStateChange`` with the
        // ``ws_token_failed`` error class via our subscriber, so the
        // chip is already red — no extra setLspChip needed here.
        return;
      }
      acquired = client;
      activeLspRef.current = client;

      // Open the document. ``openDocument`` queues internally if
      // initialize hasn't completed yet, so this is safe pre-ready.
      try {
        client.openDocument(fileUri, content, lspLang);
      } catch {
        // ignore
      }

      // Diagnostics → Monaco markers.
      unsubscribeDiagnostics = client.onDiagnostics((params) => {
        if (params.uri !== fileUri) return;
        const monaco = monacoRef.current as
          | typeof import("monaco-editor")
          | null;
        const ed = editorRef.current as
          | import("monaco-editor").editor.IStandaloneCodeEditor
          | null;
        if (!monaco || !ed) return;
        const model = ed.getModel();
        if (!model) return;
        const markers = diagnosticsToMarkers(
          params.diagnostics,
          `lsp:${lspLang}`
        );
        try {
          monaco.editor.setModelMarkers(model, `lsp:${lspLang}`, markers);
        } catch {
          // ignore
        }
      });

      // Register Monaco completion + hover providers once per language.
      const monaco = monacoRef.current as typeof import("monaco-editor") | null;
      if (monaco && !monacoProvidersRef.current.has(lspLang)) {
        const completionDisposable = monaco.languages.registerCompletionItemProvider(
          monacoLang,
          {
            triggerCharacters: [".", ":", "(", "<", "\"", "'", "/", "@"],
            provideCompletionItems: async (model, position) => {
              const current = activeLspRef.current;
              if (!current || current.language !== lspLang) return null;
              const word = model.getWordUntilPosition(position);
              const range = {
                startLineNumber: position.lineNumber,
                endLineNumber: position.lineNumber,
                startColumn: word.startColumn,
                endColumn: word.endColumn,
              };
              const list = await current.requestCompletion(
                uriForPath(model.uri.path),
                {
                  line: position.lineNumber - 1,
                  character: position.column - 1,
                }
              );
              return completionsToMonaco(list, range) ?? undefined;
            },
          }
        );
        const hoverDisposable = monaco.languages.registerHoverProvider(
          monacoLang,
          {
            provideHover: async (model, position) => {
              const current = activeLspRef.current;
              if (!current || current.language !== lspLang) return null;
              const hover = await current.requestHover(
                uriForPath(model.uri.path),
                {
                  line: position.lineNumber - 1,
                  character: position.column - 1,
                }
              );
              return hoverToMonaco(hover) ?? undefined;
            },
          }
        );
        monacoProvidersRef.current.set(lspLang, () => {
          try {
            completionDisposable.dispose();
          } catch {
            // ignore
          }
          try {
            hoverDisposable.dispose();
          } catch {
            // ignore
          }
        });
        // Track on the disposer list too so the manager-level teardown
        // catches them.
        lspProvidersDisposedRef.current.push(() => {
          monacoProvidersRef.current.get(lspLang)?.();
          monacoProvidersRef.current.delete(lspLang);
        });
      }
    })();

    return () => {
      disposed = true;
      if (unsubscribeFromManager) {
        try {
          unsubscribeFromManager();
        } catch {
          // ignore
        }
        unsubscribeFromManager = null;
      } else {
        // Detach the subscriber even if the acquire hadn't resolved
        // yet — covers the synchronous-cleanup path (StrictMode
        // double-invoke in dev) where the manager already has the
        // listener registered.
        try {
          manager.unsubscribe(lspLang, onLspState);
        } catch {
          // ignore
        }
      }
      if (unsubscribeDiagnostics) {
        try {
          unsubscribeDiagnostics();
        } catch {
          // ignore
        }
      }
      try {
        acquired?.closeDocument(fileUri);
      } catch {
        // ignore
      }
      // Clear the active LSP ref only if it still points at this file's
      // client — a faster file switch may have already swapped it.
      if (activeLspRef.current === acquired) {
        activeLspRef.current = null;
      }
      // Clear any LSP markers we set for this file so a follow-up
      // open doesn't see stale red squiggles.
      try {
        const monaco = monacoRef.current as
          | typeof import("monaco-editor")
          | null;
        const ed = editorRef.current as
          | import("monaco-editor").editor.IStandaloneCodeEditor
          | null;
        const model = ed?.getModel();
        if (monaco && model) {
          monaco.editor.setModelMarkers(model, `lsp:${lspLang}`, []);
        }
      } catch {
        // ignore
      }
    };
    // We deliberately drop ``fileQuery.data`` and ``fileBuffers`` from
    // the dep list — only the *path* should re-fire the effect; in-flight
    // edits are pushed through ``handleEditorChange`` instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, fileQuery.data?.content !== undefined]);

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
          <LspChip state={lspChip} />
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
            onMount={(editor, monaco) => {
              editorRef.current = editor;
              // P1-3 — stash the monaco namespace so the per-file LSP
              // effect can register completion / hover providers and
              // push diagnostics to ``editor.setModelMarkers``. NOTE:
              // for TypeScript, Monaco's bundled in-browser TS worker
              // is auto-activated for the ``typescript`` languageId
              // (and ``javascript``); if our LSP WS fails red, the
              // user still gets keyword + symbol completion from the
              // worker. That's the fallback the design called out
              // ("yes for TypeScript specifically — monaco-typescript
              // is already shipped as a Monaco bundle"). No extra
              // wiring needed.
              monacoRef.current = monaco;

              // FE-P4 audit fix — ``onDidPaste`` was the previous hook
              // for ``lsp_completion_accepted`` and was wrong on every
              // axis: a paste from the clipboard is NOT a completion
              // accept, and a completion accept does NOT fire paste.
              // The Monaco public API surface for "suggestion accepted"
              // varies by version; ``editor.contrib.suggestController``
              // exposes ``model.state`` (0 = Closed, 1 = Open, ...)
              // and ``acceptSelectedSuggestion`` is fired via the
              // ``Tab``/``Enter`` keystrokes while the widget is Open.
              // We listen to keydown and gate on the suggest widget
              // being visible, falling back to a no-op when Monaco's
              // contribution surface isn't available (jsdom / future
              // versions that rename the controller).
              try {
                if (typeof (editor as { onKeyDown?: unknown }).onKeyDown !== "function") {
                  // jsdom path: editor.onKeyDown isn't wired in the
                  // test bundle. Skip wiring so vitest doesn't blow
                  // up — telemetry coverage is asserted with a
                  // synthetic dispatch instead.
                } else {
                  const ed = editor as unknown as {
                    onKeyDown: (cb: (e: { code: string; keyCode?: number }) => void) => {
                      dispose(): void;
                    };
                    getContribution?: (id: string) => unknown;
                    _contributions?: Record<string, { model?: { state?: number } }>;
                  };
                  ed.onKeyDown((e) => {
                    if (e.code !== "Tab" && e.code !== "Enter") return;
                    const lsp = activeLspRef.current;
                    if (!lsp) return;
                    // Resolve the suggest controller via the public
                    // ``getContribution`` API first; fall back to the
                    // private ``_contributions`` bag because some
                    // Monaco builds drop the public method when the
                    // contribution is registered lazily.
                    let widgetOpen = false;
                    try {
                      const pub = ed.getContribution?.(
                        "editor.contrib.suggestController",
                      ) as { model?: { state?: number } } | undefined;
                      const priv = ed._contributions?.[
                        "editor.contrib.suggestController"
                      ];
                      const state = pub?.model?.state ?? priv?.model?.state;
                      // Suggest controller state: 0 = Idle/Closed, 1 = Open.
                      // Only count an accept when the widget is open;
                      // otherwise Tab/Enter is plain navigation /
                      // newline and would over-count.
                      widgetOpen = state === 1;
                      // P1 — fragility canary. If both the public
                      // ``getContribution`` API AND the private
                      // ``_contributions`` bag yield no suggest
                      // controller (or no ``model``), Monaco renamed
                      // the contribution and ``lsp_completion_accepted``
                      // will silently stop firing. Emit a one-shot
                      // dev warning so the regression is visible
                      // without a runtime crash or telemetry hole.
                      if (
                        process.env.NODE_ENV !== "production" &&
                        !monacoContribWarnedRef.current &&
                        pub?.model === undefined &&
                        priv?.model === undefined
                      ) {
                        monacoContribWarnedRef.current = true;
                        if (typeof console !== "undefined") {
                          console.warn(
                            "[CodeEditor] suggest controller not found via " +
                              "getContribution() or _contributions[]; " +
                              "lsp_completion_accepted telemetry will be " +
                              "dropped — Monaco may have renamed the " +
                              "contribution.",
                          );
                        }
                      }
                    } catch {
                      widgetOpen = false;
                    }
                    if (!widgetOpen) return;
                    try {
                      trackLspEvent("lsp_completion_accepted", {
                        language: lsp.language,
                      });
                    } catch {
                      // ignore — telemetry is best-effort
                    }
                  });
                }
              } catch {
                // ignore — fallback: completion-accepted events skipped
              }

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

/**
 * P1-3 footer chip. Mirrors the three-state design:
 *   - green: ready ("lsp · python — healthy")
 *   - amber: connecting (during cold-start)
 *   - red: error / disconnected with the structured error in a tooltip
 *
 * Lives next to ``SaveBadge`` in the editor toolbar so the visual
 * vocabulary matches the existing patterns. Hidden for files that have
 * no LSP attached (e.g. markdown, yaml).
 */
function LspChip({ state }: { state: LSPChipState }) {
  if (!state.visible || !state.language) return null;
  const base =
    "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[10px] tabular-nums";
  if (state.kind === "ready") {
    return (
      <span
        className={cn(
          base,
          "border-[color-mix(in_oklch,var(--color-success)_30%,transparent)] bg-[color-mix(in_oklch,var(--color-success)_10%,transparent)] text-[var(--color-success)]"
        )}
        role="status"
        aria-live="polite"
        title={`lsp · ${state.language} — healthy`}
        data-testid="lsp-chip"
        data-state="ready"
      >
        <span
          aria-hidden
          className="inline-block size-1.5 rounded-full bg-[var(--color-success)]"
        />
        lsp · {state.language}
      </span>
    );
  }
  if (state.kind === "connecting") {
    return (
      <span
        className={cn(
          base,
          "border-[color-mix(in_oklch,var(--color-warning)_30%,transparent)] bg-[color-mix(in_oklch,var(--color-warning)_10%,transparent)] text-[var(--color-warning)]"
        )}
        role="status"
        aria-live="polite"
        title={`lsp · ${state.language} — connecting…`}
        data-testid="lsp-chip"
        data-state="connecting"
      >
        <Loader2 className="size-3 animate-spin" aria-hidden />
        lsp · {state.language}
      </span>
    );
  }
  // error or disconnected → red
  const errorLabel = state.error ?? "unavailable";
  return (
    <span
      className={cn(
        base,
        "border-[color-mix(in_oklch,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklch,var(--color-danger)_10%,transparent)] text-[var(--color-danger)]"
      )}
      role="status"
      aria-live="polite"
      title={`lsp · ${state.language} — unavailable (${errorLabel})`}
      data-testid="lsp-chip"
      data-state="error"
      data-error={errorLabel}
    >
      <span
        aria-hidden
        className="inline-block size-1.5 rounded-full bg-[var(--color-danger)]"
      />
      lsp · {state.language}
    </span>
  );
}
