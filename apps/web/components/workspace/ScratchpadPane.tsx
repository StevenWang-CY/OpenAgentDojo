"use client";

/**
 * P1-4 — Scratchpad pane.
 *
 * Per-session free-text buffer the user can use to jot reasoning while
 * working the mission. Lives at the bottom of the AgentChat column so it's
 * adjacent to the surface where the user is prompting the agent.
 *
 * Design constraints (see P1_DESIGN.md §"Frontend surface"):
 *   - raw text only in MVP (no markdown preview)
 *   - 32 KiB cap, enforced both client-side (input clamp + toast) and
 *     server-side (PUT returns 413)
 *   - autosave 1.5 s after the last keystroke; the header shows
 *     "saving…" / "saved · 12s ago" status
 *   - collapsible; collapsed state persists per (user, session)
 *   - cmd/ctrl+B toggles the pane (parent wires the global shortcut)
 *
 * The component is intentionally read-only/null-rendered when the session
 * isn't active (graded / submitting / abandoned / error / provisioning) —
 * the BE returns 409 on PUT, but defending here keeps the UI honest. The
 * pane also never renders for non-owners (the parent gates on the
 * me.id === session.user_id check; this component just defends with a
 * silent 403 → render-nothing branch).
 */

import * as React from "react";
import { ChevronDown, ChevronUp, Loader2, Pencil } from "lucide-react";
import { toast } from "sonner";
import {
  ApiError,
  getSessionNote,
  putSessionNote,
} from "@/lib/api";
import { env } from "@/lib/env";
import { useWorkspaceStore } from "@/stores/workspaceStore";
import { track } from "@/lib/telemetry";
import { cn } from "@/lib/utils";

/** Server-side cap. Mirrors ``apps/api/app/sessions/notes.py::LIMIT_BYTES``. */
export const SCRATCHPAD_LIMIT_BYTES = 32_768;
/** Soft warning threshold (≈97% of the cap) — shows the "near limit" chip. */
const SCRATCHPAD_WARN_BYTES = 31_744;
/** Autosave debounce after the last keystroke (ms). */
const AUTOSAVE_DEBOUNCE_MS = 1_500;

export interface ScratchpadPaneProps {
  /** Owning session id — required to issue GET/PUT. */
  sessionId: string;
  /**
   * Current session lifecycle status. When anything other than ``active``
   * (e.g. ``submitting``, ``graded``), input is disabled and a banner
   * explains why. We accept ``undefined`` so the pane can render the
   * loading shimmer while the session query is in flight.
   */
  sessionStatus?: string;
  /** Optional className appended to the outer wrapper. */
  className?: string;
}

interface SaveState {
  kind: "idle" | "saving" | "saved" | "error";
  /** Epoch ms of the last successful save — drives the "saved · 12s ago" hint. */
  lastSavedAt: number | null;
  /** Human-friendly error message (only set when ``kind === "error"``). */
  message?: string;
}

const utf8Encoder = new TextEncoder();

/** UTF-8 byte length — needed because the BE caps on bytes, not characters. */
function byteLength(s: string): number {
  return utf8Encoder.encode(s).length;
}

/**
 * P1 — best-effort final flush via ``navigator.sendBeacon``.
 *
 * The React cleanup path already issues a fire-and-forget ``putSessionNote``
 * fetch, but a tab close + flaky network drops the request before the
 * browser unloads the page (the userland fetch is cancelled along with the
 * document). ``sendBeacon`` is the only transport guaranteed to survive
 * an unload — the browser queues the request to the OS network stack
 * and lets it drain after the document is gone. We mirror the regular
 * PUT body ``{body}`` and lean on the BE's idempotent overwrite so a
 * duplicate write (beacon + cleanup PUT both succeed) is harmless.
 *
 * The endpoint is the same ``PUT /api/v1/sessions/{id}/note`` as the
 * regular autosave, but ``sendBeacon`` is POST-only — the backend
 * accepts the duplicate POST as well via the same router (see
 * ``apps/api/app/sessions/notes.py``); if the route is PUT-only the
 * beacon will 405 and we silently fall through to the cleanup PUT. The
 * surface is intentionally narrow: this is a backup, not the primary
 * write path.
 *
 * Returns ``true`` if the beacon was queued, ``false`` if the browser
 * refused (over the 64 KB per-beacon quota, or the API is missing in
 * jsdom / SSR).
 */
function sendNoteBeacon(sessionId: string, body: string): boolean {
  if (typeof navigator === "undefined" || typeof navigator.sendBeacon !== "function") {
    return false;
  }
  try {
    const url = `${env.apiBaseUrl}/api/v1/sessions/${encodeURIComponent(
      sessionId,
    )}/note`;
    const blob = new Blob([JSON.stringify({ body })], {
      type: "application/json",
    });
    return navigator.sendBeacon(url, blob);
  } catch {
    return false;
  }
}

/**
 * Clamp a draft string to ``maxBytes`` UTF-8 bytes. Cuts on a UTF-16 code-unit
 * boundary so we never split a multi-byte character mid-sequence. Callers use
 * this to enforce the 32 KiB cap as the user types — the BE enforces the same
 * limit and returns 413 if a clock-skew slips through.
 */
export function clampToBytes(s: string, maxBytes: number): string {
  if (byteLength(s) <= maxBytes) return s;
  // Binary search over the string length so the work is O(log n) rather
  // than O(n²) for the naïve "trim one char at a time" loop.
  let lo = 0;
  let hi = s.length;
  while (lo < hi) {
    const mid = (lo + hi + 1) >>> 1;
    if (byteLength(s.slice(0, mid)) <= maxBytes) {
      lo = mid;
    } else {
      hi = mid - 1;
    }
  }
  return s.slice(0, lo);
}

export function ScratchpadPane({
  sessionId,
  sessionStatus,
  className,
}: ScratchpadPaneProps) {
  const store = useWorkspaceStore(sessionId);
  const open = store((s) => s.scratchpadOpen);
  const setOpen = store((s) => s.setScratchpadOpen);
  const setScratchpadBytes = store((s) => s.setScratchpadBytes);
  // Post-reset hook: ``session.reset`` is emitted by the BE on
  // ``POST /sessions/{id}/reset``. The BE-side tests confirm the scratchpad
  // body survives the reset, but the FE still re-fetches so a separately
  // edited note (e.g. another tab wrote during the reset round-trip) shows
  // up authoritatively. The selector reads the highest reset event id so a
  // change to the count re-triggers the GET below.
  const lastResetEventId = store((s) => {
    let max = 0;
    for (const e of s.events) {
      if (e.event_type === "session.reset" && e.id > max) max = e.id;
    }
    return max;
  });

  // Input ref so the textarea autofocuses when the user expands the pane.
  const textareaRef = React.useRef<HTMLTextAreaElement | null>(null);

  // Draft mirrors what's in the textarea; lastSavedRef records the last
  // body we successfully PUT (for the dirty check). Keeping them as
  // separate state vs ref lets us avoid re-rendering on every saved-at tick.
  const [draft, setDraft] = React.useState<string>("");
  const lastSavedRef = React.useRef<string>("");
  const [saveState, setSaveState] = React.useState<SaveState>({
    kind: "idle",
    lastSavedAt: null,
  });
  // ``loaded`` flips true once the initial GET resolves (success or 404 →
  // empty). The textarea stays disabled-loading until that happens so a
  // user who opens the pane and types before the server reply doesn't get
  // their input overwritten by the GET result on arrival.
  const [loaded, setLoaded] = React.useState(false);
  // Mirrors the server-reported "session not active" state. Distinct from
  // the prop-derived ``sessionStatus`` because the prop may lag while a
  // 409 in flight already tells us writes are locked.
  const [sessionInactiveServer, setSessionInactiveServer] = React.useState(false);
  // Forfeit any further input once a 413 lands so the user has to delete
  // text before the autosave will resume.
  const [capExceeded, setCapExceeded] = React.useState(false);

  // Surface telemetry when the pane is first rendered visible. We fire on
  // every collapsed→expanded transition so the analytics funnel can
  // attribute discovery (button click) vs. re-open (kbd / next session).
  //
  // The ``trigger`` source is pulled from the store at the moment of the
  // open transition — the keybind/button surfaces stash it transiently via
  // ``setScratchpadOpen(open, trigger)``; if nothing was set (e.g. a
  // restore-from-persisted-state on mount) we fall through to "button" as
  // the conservative default.
  const lastTriggerRef = store((s) => s.lastScratchpadTrigger);
  const lastOpenRef = React.useRef<boolean>(open);
  React.useEffect(() => {
    // Re-fire only on the false→true transition; idle re-renders shouldn't
    // duplicate the event. The telemetry helper itself debounces 1s of
    // identical events, so even a flap can't spam PostHog.
    if (open && !lastOpenRef.current) {
      track("scratchpad_opened", {
        session_id: sessionId,
        trigger: lastTriggerRef ?? "button",
      });
    }
    lastOpenRef.current = open;
  }, [open, sessionId, lastTriggerRef]);

  // ── Initial GET on mount ──────────────────────────────────────────────────
  // Always fetches once even if the pane is collapsed — the byte count is
  // needed for the AgentChat "viewed during prompt" emission. Cancel on
  // unmount via AbortController so a navigation away mid-flight doesn't
  // leak the request.
  React.useEffect(() => {
    const ac = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        const note = await getSessionNote(sessionId, ac.signal);
        if (cancelled) return;
        setDraft(note.body);
        lastSavedRef.current = note.body;
        setScratchpadBytes(byteLength(note.body));
        setSaveState({
          kind: "idle",
          lastSavedAt: note.updated_at ? new Date(note.updated_at).getTime() : null,
        });
        setLoaded(true);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError) {
          if (err.status === 403) {
            // Non-owner — the parent shouldn't have mounted us, but defend
            // by collapsing into a no-render. Returning here without
            // flipping loaded leaves the surface in its skeleton state;
            // the empty render below short-circuits via ``status === 403``.
            setSaveState({
              kind: "error",
              lastSavedAt: null,
              message: "forbidden",
            });
            setLoaded(true);
            return;
          }
          if (err.status === 409) {
            // Session already terminal: render the read-only banner.
            setSessionInactiveServer(true);
            setLoaded(true);
            return;
          }
        }
        // Network failure / 5xx — leave the textarea empty but mark loaded
        // so the user can still type (an empty scratchpad is the right
        // default). The error indicator surfaces the failure inline.
        setSaveState({
          kind: "error",
          lastSavedAt: null,
          message: "couldn't load notes",
        });
        setLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
    };
    // Re-runs when ``lastResetEventId`` bumps (a new session.reset event
    // landed). The BE preserves the body across reset; we re-fetch to
    // reconcile against any concurrent edit from another tab.
  }, [sessionId, setScratchpadBytes, lastResetEventId]);

  // ── Autosave (debounced PUT) ──────────────────────────────────────────────
  // We use a manual setTimeout chain rather than ``useDebounce`` so we can
  // cancel pending flushes on unmount, on session-status flips, and on the
  // post-413 hard-stop. ``saveTimerRef`` holds the active timer id; the
  // inflightRef guards against overlapping PUTs (a slow network + fast
  // typing could otherwise queue several in flight at once).
  const saveTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const inflightRef = React.useRef<boolean>(false);
  const debounceStartRef = React.useRef<number | null>(null);
  // Item 20 — track the active autosave AbortController so an unmount
  // mid-flight cancels the request cleanly. AbortError is swallowed in
  // the flush catch block (the user navigated away — a toast would be
  // noise).
  const inflightAbortRef = React.useRef<AbortController | null>(null);

  const cancelPendingSave = React.useCallback(() => {
    if (saveTimerRef.current !== null) {
      clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
  }, []);

  const flushSave = React.useCallback(
    async (body: string) => {
      if (inflightRef.current) {
        // Re-arm: a save just landed; let the next keystroke schedule the
        // follow-up. Avoids stacking concurrent PUTs.
        return;
      }
      if (body === lastSavedRef.current) {
        setSaveState((prev) =>
          prev.kind === "saving" ? { ...prev, kind: "idle" } : prev,
        );
        return;
      }
      inflightRef.current = true;
      // Item 20 — fresh AbortController per flush so unmount can cancel
      // this single request without affecting a follow-up PUT that may
      // have been kicked off in the meantime.
      const ac = new AbortController();
      inflightAbortRef.current = ac;
      setSaveState({ kind: "saving", lastSavedAt: Date.now() });
      const debouncedMs =
        debounceStartRef.current === null
          ? AUTOSAVE_DEBOUNCE_MS
          : Math.max(0, Date.now() - debounceStartRef.current);
      try {
        const result = await putSessionNote(sessionId, body, {
          signal: ac.signal,
        });
        lastSavedRef.current = body;
        setScratchpadBytes(byteLength(body));
        setSaveState({
          kind: "saved",
          lastSavedAt: result.updated_at
            ? new Date(result.updated_at).getTime()
            : Date.now(),
        });
        setCapExceeded(false);
        track("scratchpad_edit_persisted", {
          session_id: sessionId,
          bytes: byteLength(body),
          debounced_ms: debouncedMs,
        });
      } catch (err) {
        // Item 20 — AbortError means the user navigated away mid-flight.
        // Treat as a no-op so the cleanup path doesn't toast a phantom
        // failure. ``request`` may surface aborts as either a DOMException
        // or wrap them in ApiError(status=0); handle both.
        if (
          err instanceof DOMException && err.name === "AbortError"
        ) {
          return;
        }
        if (err instanceof ApiError) {
          if (err.status === 413) {
            setCapExceeded(true);
            toast.error(
              "Scratchpad is full — copy somewhere safe and trim.",
              { id: "scratchpad-full" },
            );
            setSaveState({
              kind: "error",
              lastSavedAt: lastSavedRef.current === body ? Date.now() : null,
              message: "over 32 KB",
            });
          } else if (err.status === 409) {
            // Item 31 — only the canonical ``session_not_active`` 409
            // disables input. Other 409 codes (rate-limit, write
            // conflict, etc.) keep the textarea editable so the user
            // can retry without losing their draft.
            //
            // FastAPI's standard HTTPException envelope nests the code
            // under ``detail.code`` (per api.ts JSDoc), but a few BE
            // call sites legacy-stringify the detail and put ``code``
            // at the top level of the body. Accept either shape so the
            // FE stays compatible across both.
            const body = err.body as
              | { detail?: unknown; code?: unknown }
              | null
              | undefined;
            const detail = body?.detail;
            const code: string | undefined =
              typeof detail === "object" &&
              detail !== null &&
              typeof (detail as { code?: unknown }).code === "string"
                ? ((detail as { code: string }).code)
                : typeof body?.code === "string"
                  ? (body.code as string)
                  : undefined;
            if (code === "session_not_active") {
              setSessionInactiveServer(true);
              setSaveState({
                kind: "error",
                lastSavedAt: null,
                message: "session ended",
              });
            } else {
              toast.error(
                "Couldn't save your scratchpad — retry in a moment.",
                { id: "scratchpad-conflict" },
              );
              setSaveState({
                kind: "error",
                lastSavedAt: null,
                message: "save failed",
              });
            }
          } else if (err.status === 403) {
            setSaveState({
              kind: "error",
              lastSavedAt: null,
              message: "forbidden",
            });
          } else {
            setSaveState({
              kind: "error",
              lastSavedAt: null,
              message: err.message || "save failed",
            });
          }
        } else {
          setSaveState({
            kind: "error",
            lastSavedAt: null,
            message: "save failed",
          });
        }
      } finally {
        inflightRef.current = false;
        debounceStartRef.current = null;
        if (inflightAbortRef.current === ac) {
          inflightAbortRef.current = null;
        }
      }
    },
    [sessionId, setScratchpadBytes],
  );

  // Schedule a flush 1.5 s after the latest change — cancels any pending
  // flush so a fast typist accumulates into one PUT instead of N.
  const scheduleSave = React.useCallback(
    (body: string) => {
      cancelPendingSave();
      if (debounceStartRef.current === null) {
        debounceStartRef.current = Date.now();
      }
      saveTimerRef.current = setTimeout(() => {
        saveTimerRef.current = null;
        void flushSave(body);
      }, AUTOSAVE_DEBOUNCE_MS);
    },
    [cancelPendingSave, flushSave],
  );

  // We keep the latest draft + last-saved values in refs so the unmount
  // cleanup (Item 30) can decide whether a final flush is needed without
  // re-running on every keystroke.
  const draftRef = React.useRef(draft);
  React.useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  // Hard tear-down on unmount: cancel the pending timer, abort any
  // in-flight PUT (Item 20), then fire a best-effort final flush
  // (Item 30) when the draft has unsaved edits. The flush is genuinely
  // fire-and-forget — React won't await an async cleanup — and the BE
  // accepts a duplicate write idempotently.
  //
  // P1 — alongside the fetch flush we also queue a ``navigator.sendBeacon``
  // backup so a tab close + flaky network combo doesn't drop the edit.
  // The same beacon is fired from a ``beforeunload`` listener for the
  // "user closed the tab without unmounting React" path (component
  // cleanup doesn't run on a hard navigate-away). BE accepts the
  // duplicate write idempotently.
  React.useEffect(() => {
    const beforeUnload = (): void => {
      const currentDraft = draftRef.current;
      if (currentDraft === lastSavedRef.current) return;
      sendNoteBeacon(sessionId, currentDraft);
    };
    if (typeof window !== "undefined") {
      window.addEventListener("beforeunload", beforeUnload);
    }
    return () => {
      if (typeof window !== "undefined") {
        window.removeEventListener("beforeunload", beforeUnload);
      }
      cancelPendingSave();
      // Abort the in-flight autosave so an aborted-but-resolved fetch
      // doesn't race the final flush below. The catch in flushSave
      // swallows AbortError so no error state is set.
      if (inflightAbortRef.current) {
        inflightAbortRef.current.abort();
        inflightAbortRef.current = null;
      }
      // Final flush — only when the latest draft hasn't already been
      // persisted. We deliberately ignore errors: the user has left
      // the surface, surfacing a toast is impossible, and the BE is
      // idempotent so a duplicate write is harmless.
      const currentDraft = draftRef.current;
      if (currentDraft !== lastSavedRef.current) {
        // Backup transport first — sendBeacon survives unload even
        // when the fetch below gets cancelled by the page tear-down.
        // The fetch then races against it; whichever lands first
        // wins, and the BE de-dupes by overwrite.
        sendNoteBeacon(sessionId, currentDraft);
        void putSessionNote(sessionId, currentDraft).catch(() => {
          /* fire-and-forget — see comment above */
        });
      }
    };
  }, [cancelPendingSave, sessionId]);

  // ── Status-derived disable flags ──────────────────────────────────────────
  // ``inactive`` is the union of "the prop said the session isn't active" and
  // "a PUT just told us 409". Either disables input and surfaces the banner.
  const inactive =
    sessionInactiveServer ||
    (sessionStatus !== undefined && sessionStatus !== "active");
  const disabledForInput = inactive || !loaded;

  // ── Input change handler ──────────────────────────────────────────────────
  const handleChange = React.useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      if (disabledForInput) return;
      let next = e.target.value;
      // Hard-cap on the way in — anything past the limit is silently
      // dropped at the boundary. Pair this with the cap-exceeded toast
      // so the user understands why their keystrokes aren't landing.
      const nextBytes = byteLength(next);
      if (nextBytes > SCRATCHPAD_LIMIT_BYTES) {
        next = clampToBytes(next, SCRATCHPAD_LIMIT_BYTES);
        if (!capExceeded) {
          setCapExceeded(true);
          toast.error(
            "Scratchpad is full — copy somewhere safe and trim.",
            { id: "scratchpad-full" },
          );
        }
      } else if (capExceeded && nextBytes <= SCRATCHPAD_LIMIT_BYTES) {
        // User trimmed back under the cap; re-allow autosave.
        setCapExceeded(false);
      }
      setDraft(next);
      // Update the byte-count side-channel optimistically so the AgentChat
      // composer-focus check is in sync with what the user sees. The PUT
      // result will reconcile it on success.
      setScratchpadBytes(byteLength(next));
      scheduleSave(next);
    },
    [
      capExceeded,
      disabledForInput,
      scheduleSave,
      setScratchpadBytes,
    ],
  );

  // Autofocus on expand so cmd/ctrl+B → type just works.
  React.useEffect(() => {
    if (open && loaded && !disabledForInput) {
      // Tiny rAF to wait for the layout transition to finish.
      const id = requestAnimationFrame(() => {
        textareaRef.current?.focus({ preventScroll: true });
      });
      return () => cancelAnimationFrame(id);
    }
  }, [open, loaded, disabledForInput]);

  // ── Render ────────────────────────────────────────────────────────────────
  // Defensive 403 short-circuit: render nothing. The parent should already
  // be gating on owner-ness, but this keeps a malformed mount silent.
  if (saveState.kind === "error" && saveState.message === "forbidden") {
    return null;
  }

  const bytes = byteLength(draft);
  const showWarnChip = bytes >= SCRATCHPAD_WARN_BYTES && bytes < SCRATCHPAD_LIMIT_BYTES;
  const atCap = bytes >= SCRATCHPAD_LIMIT_BYTES;

  return (
    <section
      aria-label="Scratchpad notes"
      data-testid="scratchpad-pane"
      className={cn(
        "flex flex-col border-t border-[var(--color-border)] bg-[var(--color-surface)]",
        className,
      )}
    >
      <ScratchpadHeader
        open={open}
        onToggle={() => setOpen(!open, "button")}
        saveState={saveState}
        bytes={bytes}
        showWarnChip={showWarnChip}
        atCap={atCap}
        sessionInactive={inactive}
      />
      {open ? (
        <div
          id="scratchpad-body"
          className="flex flex-col"
          // 240px sweet-spot per design (220-280px range).
          style={{ height: "240px" }}
        >
          {inactive ? (
            <ReadOnlyBanner />
          ) : null}
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={handleChange}
            disabled={disabledForInput}
            // Item 21 — surface the read-only state via aria-readonly so
            // screen readers announce the inactive session correctly.
            // ``disabled`` already prevents input but doesn't communicate
            // the *reason* (forbidden vs. session ended vs. loading); the
            // aria attribute pairs with the ReadOnlyBanner's announcement
            // for a coherent SR experience.
            aria-readonly={inactive}
            spellCheck={false}
            aria-label="Scratchpad — your private notes for this session"
            data-testid="scratchpad-textarea"
            placeholder={
              loaded
                ? "// jot reasoning, ruled-out hypotheses, things to verify after the patch lands…"
                : "// loading notes…"
            }
            className={cn(
              "flex-1 min-h-0 resize-none border-0 bg-[var(--color-surface)]",
              "px-3 py-2 font-mono text-xs leading-relaxed text-[var(--color-foreground)]",
              // Tab-width 2 matches the dojo editor convention; -moz prefix
              // for legacy Firefox.
              "[tab-size:2] [-moz-tab-size:2]",
              "placeholder:text-[var(--color-muted-foreground)]",
              "focus:outline-none focus-visible:outline-none",
              "disabled:cursor-not-allowed disabled:opacity-60",
            )}
          />
        </div>
      ) : null}
    </section>
  );
}

interface ScratchpadHeaderProps {
  open: boolean;
  onToggle(): void;
  saveState: SaveState;
  bytes: number;
  showWarnChip: boolean;
  atCap: boolean;
  sessionInactive: boolean;
}

function ScratchpadHeader({
  open,
  onToggle,
  saveState,
  bytes,
  showWarnChip,
  atCap,
  sessionInactive,
}: ScratchpadHeaderProps) {
  // Light tick so "saved · Ns ago" stays fresh without re-rendering the
  // textarea. Only mounted when there's a saved-at timestamp to display.
  const [, forceTick] = React.useReducer((n: number) => n + 1, 0);
  React.useEffect(() => {
    if (saveState.kind !== "saved" || saveState.lastSavedAt === null) return;
    const id = window.setInterval(() => forceTick(), 5_000);
    return () => window.clearInterval(id);
  }, [saveState.kind, saveState.lastSavedAt]);

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      aria-controls="scratchpad-body"
      data-testid="scratchpad-toggle"
      className={cn(
        "flex items-center justify-between gap-3 px-3 py-1.5",
        "border-b border-transparent",
        "hover:bg-[var(--color-surface-elevated)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--color-surface)]",
        open ? "border-b-[var(--color-border)]" : null,
      )}
    >
      <span className="flex items-center gap-1.5 font-mono text-[11px] text-[var(--color-muted-foreground)]">
        {open ? (
          <ChevronDown className="size-3" aria-hidden />
        ) : (
          <ChevronUp className="size-3" aria-hidden />
        )}
        <span>{"// notes"}</span>
        {/*
         * P2 — design contract calls out a CodeMirror 6 markdown editor;
         * we ship a raw <textarea> instead to keep the bundle lean (a
         * full CM6 markdown stack adds ~120 KB to the chunk and the
         * autosave / cap / debounce semantics are identical either way).
         * The chip below tells the user the text is rendered as
         * markdown downstream — specifically in the post-mortem
         * coaching reflection — so they understand the lightweight
         * monospace surface isn't the final read view.
         */}
        <span
          data-testid="scratchpad-markdown-chip"
          aria-label="Markdown rendered in post-mortem"
          title="Renders as markdown in the post-mortem reflection."
          className="ml-1 inline-flex items-center rounded-sm border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-1 py-0 text-[10px] uppercase tracking-wide text-[var(--color-muted-foreground)]/80"
        >
          // markdown
        </span>
        {!open && bytes > 0 ? (
          <span
            className="text-[var(--color-muted-foreground)]/70"
            data-testid="scratchpad-byte-count-collapsed"
          >
            · {formatBytes(bytes)}
          </span>
        ) : null}
      </span>
      <span
        className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-wide text-[var(--color-muted-foreground)]"
        aria-live="polite"
      >
        {showWarnChip ? (
          <span
            data-testid="scratchpad-near-limit"
            className="inline-flex items-center gap-1 rounded-sm border border-[var(--color-warning)] bg-[oklch(from_var(--color-warning)_l_c_h/0.12)] px-1.5 py-0.5 text-[var(--color-warning)]"
          >
            near limit
          </span>
        ) : null}
        {atCap ? (
          <span
            data-testid="scratchpad-at-limit"
            className="inline-flex items-center gap-1 rounded-sm border border-[var(--color-danger)] bg-[oklch(from_var(--color-danger)_l_c_h/0.12)] px-1.5 py-0.5 text-[var(--color-danger)]"
          >
            full
          </span>
        ) : null}
        {sessionInactive ? (
          <span className="text-[var(--color-warning)]">read only</span>
        ) : (
          <SaveStatusIndicator state={saveState} />
        )}
        <span aria-hidden className="opacity-60">
          {open ? "▾ collapse" : "▸ expand"}
        </span>
      </span>
    </button>
  );
}

function SaveStatusIndicator({ state }: { state: SaveState }) {
  if (state.kind === "saving") {
    return (
      <span
        className="inline-flex items-center gap-1 text-[var(--color-muted-foreground)]"
        data-testid="scratchpad-saving"
      >
        <Loader2
          className="size-3 animate-spin motion-reduce:animate-none"
          aria-hidden
        />
        saving…
      </span>
    );
  }
  if (state.kind === "saved" && state.lastSavedAt !== null) {
    return (
      <span
        className="text-[var(--color-muted-foreground)]"
        data-testid="scratchpad-saved"
        title={new Date(state.lastSavedAt).toISOString()}
      >
        saved · {formatSecondsAgo(state.lastSavedAt)}
      </span>
    );
  }
  if (state.kind === "error" && state.message) {
    return (
      <span
        className="inline-flex items-center gap-1 text-[var(--color-danger)]"
        data-testid="scratchpad-error"
      >
        <Pencil className="size-3" aria-hidden />
        {state.message}
      </span>
    );
  }
  if (state.kind === "idle" && state.lastSavedAt !== null) {
    return (
      <span
        className="text-[var(--color-muted-foreground)]"
        data-testid="scratchpad-saved-idle"
      >
        saved · {formatSecondsAgo(state.lastSavedAt)}
      </span>
    );
  }
  return null;
}

function ReadOnlyBanner() {
  return (
    <p
      role="status"
      // Item 21 — polite SR announcement on the transition into the
      // read-only state so the user hears why their typing stopped
      // landing. Pairs with ``aria-readonly`` on the textarea above.
      aria-live="polite"
      className="border-b border-[var(--color-border)] bg-[oklch(from_var(--color-warning)_l_c_h/0.08)] px-3 py-1.5 text-[11px] text-[var(--color-warning)]"
      data-testid="scratchpad-inactive-banner"
    >
      Session is no longer active — notes are preserved but cannot be edited.
    </p>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

/** "12s ago" / "3m ago" / "1h ago" — terse for the header chip. */
function formatSecondsAgo(epochMs: number): string {
  const diff = Math.max(0, Math.floor((Date.now() - epochMs) / 1000));
  if (diff < 5) return "just now";
  if (diff < 60) return `${diff}s ago`;
  const min = Math.floor(diff / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  return `${hr}h ago`;
}
