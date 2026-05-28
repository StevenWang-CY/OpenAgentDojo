"use client";

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { AgentTurn, SupervisionEvent } from "@arena/shared-types";

/**
 * P1-4 (audit Item 29) — the trigger source for the most recent
 * ``setScratchpadOpen(true, …)`` call. Pushed by the pane's header
 * button (``"button"``), the workspace keybind (``"keybind"``), or a
 * deep link (``"deeplink"``); read by the pane on the next
 * false→true transition so the ``scratchpad_opened`` telemetry event
 * carries the actual surface the user touched. Transient — never
 * persisted across reloads.
 */
export type ScratchpadOpenTrigger = "button" | "keybind" | "deeplink";

/**
 * Workspace state is keyed *per session* in localStorage so a user can have
 * multiple workspaces open in different tabs without them stomping on each
 * other. The hook `useWorkspaceStore(sessionId)` returns a memoised store
 * scoped to that sessionId.
 */

export interface WorkspaceState {
  sessionId: string;
  selectedContext: string[];
  openTabs: string[];
  activeFile: string | null;
  /** Map path → user-editable content (mirrors what's in Monaco). */
  fileBuffers: Record<string, string>;
  agentTurns: AgentTurn[];
  events: SupervisionEvent[];
  /**
   * Largest `event.id` we've ever appended. Drives `?last_id=` on WS
   * reconnect so the backend can replay anything we missed during the
   * brief network gap.
   */
  lastEventId: number;
  /** Sandbox driver reported by the API — drives the local-driver banner. */
  sandboxDriver: "docker" | "local" | "unknown";

  // ── P0-9 — Quick-open / find-in-files / help overlay ─────────────────────
  /** True when the Cmd/Ctrl+P file palette is mounted. */
  commandPaletteOpen: boolean;
  /** True when the Cmd/Ctrl+Shift+F find-in-files panel is mounted. */
  searchPanelOpen: boolean;
  /** True when the ``?`` help overlay is mounted. */
  helpOverlayOpen: boolean;
  /**
   * Editor line to scroll/focus when the active file mounts. Drives the
   * "jump to match" handoff from the find-in-files panel to Monaco. Cleared
   * by the editor once the focus has been honoured so a later open of the
   * same file doesn't keep stealing focus.
   */
  activeLineFocus: number | null;

  // ── P1-4 — Scratchpad pane (notes) ───────────────────────────────────────
  /**
   * True when the bottom-of-AgentChat scratchpad pane is expanded.
   *
   * Persisted per (user, session) via the persist middleware so the user's
   * preference survives reloads. Default ``false`` for new sessions; the
   * design ships the pane collapsed so it doesn't steal vertical real estate
   * from users who never take notes.
   */
  scratchpadOpen: boolean;
  /**
   * UTF-8 byte length of the latest persisted scratchpad body. This is the
   * ONLY piece of scratchpad state the FE stores locally — the body itself
   * lives server-side, where the post-mortem reflection reads it. Storing
   * the body in the client would create a sync nightmare (multi-tab races,
   * stale buffers after a reset). The byte count is just enough for
   * AgentChat's "viewed during prompt" emission to decide whether to fire.
   */
  scratchpadBytes: number;
  /**
   * Transient — surface the latest ``setScratchpadOpen`` call originated
   * from. Read by the ScratchpadPane on a false→true transition so the
   * ``scratchpad_opened`` telemetry event carries the user's actual
   * intent (button click vs. keybind vs. deeplink). NOT persisted; defaults
   * to ``null`` on mount + after every full reset.
   */
  lastScratchpadTrigger: ScratchpadOpenTrigger | null;

  // ── Mutators ──────────────────────────────────────────────────────────────
  setSelectedContext(paths: string[]): void;
  toggleContextPath(path: string): void;
  openFile(path: string): void;
  closeTab(path: string): void;
  setActiveFile(path: string | null): void;
  setActiveFileContent(path: string, content: string): void;
  pushAgentTurn(turn: AgentTurn): void;
  pushEvent(event: SupervisionEvent): void;
  setSandboxDriver(driver: "docker" | "local" | "unknown"): void;
  setCommandPaletteOpen(open: boolean): void;
  setSearchPanelOpen(open: boolean): void;
  setHelpOverlayOpen(open: boolean): void;
  setActiveLineFocus(line: number | null): void;
  /**
   * Expand/collapse the scratchpad pane (P1-4). ``trigger`` records the
   * surface the user touched so the pane can attribute the
   * ``scratchpad_opened`` telemetry event correctly. Optional — callers
   * who don't pass one are treated as a button click (the discoverable
   * default surface).
   */
  setScratchpadOpen(open: boolean, trigger?: ScratchpadOpenTrigger): void;
  /** Record the latest persisted scratchpad byte length (P1-4). */
  setScratchpadBytes(bytes: number): void;
  /**
   * Combined: set the active file AND the line focus in a single render.
   * Used by the find-in-files panel's "jump to match" affordance — splitting
   * into two store writes would race the editor's mount.
   */
  setActivePath(path: string, line?: number | null): void;
  reset(): void;
  /**
   * P0-12 — surgical clear used by the "Reset workspace" dialog. Drops
   * fileBuffers + closes open tabs (the post-reset tree no longer
   * matches the cached buffers), but PRESERVES selectedContext (the
   * user's intent persists across resets) and events / agentTurns (the
   * supervision timeline must survive the backtrack — that's the whole
   * point of P0-12). Distinct from ``reset()`` which wipes everything
   * for a fresh session.
   */
  resetForWorkspaceReset(): void;
}

type StoreFactory = ReturnType<typeof makeWorkspaceStore>;

/**
 * Bounded LRU of per-session store factories. The previous implementation
 * grew without bound — fine for a single tab, but problematic in long-lived
 * dev sessions or tests that mount many sessions. We evict the oldest store
 * once we go over `MAX_STORES`; eviction is best-effort because Zustand
 * factories are cheap to recreate.
 *
 * Keyed by ``${userId}:${sessionId}`` so a user-switch on the same machine
 * (e.g. sign-out → sign-in as someone else) doesn't reuse the previous
 * user's persisted slice — that would have leaked workspace state across
 * accounts. ``"anon"`` is the conservative default when the caller hasn't
 * threaded a user id through yet.
 */
const MAX_STORES = 16;
const stores = new Map<string, StoreFactory>();

function storeKey(userId: string | null | undefined, sessionId: string): string {
  return `${userId ?? "anon"}:${sessionId}`;
}

function makeWorkspaceStore(sessionId: string, userId: string | null | undefined) {
  // P1 — Scratchpad localStorage scoping fix: include ``userId`` in the
  // persist name so two accounts on the same machine don't share the
  // workspace slice. Default ``"anon"`` matches ``storeKey`` so the
  // un-authed FE bootstrap and the eventual signed-in mount agree on a
  // single, stable key.
  const persistName = `arena:workspace:${userId ?? "anon"}:${sessionId}`;
  return create<WorkspaceState>()(
    persist(
      (set) => ({
        sessionId,
        selectedContext: [],
        openTabs: [],
        activeFile: null,
        fileBuffers: {},
        agentTurns: [],
        events: [],
        lastEventId: 0,
        sandboxDriver: "unknown",
        commandPaletteOpen: false,
        searchPanelOpen: false,
        helpOverlayOpen: false,
        activeLineFocus: null,
        scratchpadOpen: false,
        scratchpadBytes: 0,
        lastScratchpadTrigger: null,

        setSelectedContext(paths) {
          set({ selectedContext: dedupe(paths) });
        },
        toggleContextPath(path) {
          set((state) => {
            const exists = state.selectedContext.includes(path);
            return {
              selectedContext: exists
                ? state.selectedContext.filter((p) => p !== path)
                : [...state.selectedContext, path],
            };
          });
        },
        openFile(path) {
          set((state) => ({
            openTabs: state.openTabs.includes(path)
              ? state.openTabs
              : [...state.openTabs, path],
            activeFile: path,
          }));
        },
        closeTab(path) {
          set((state) => {
            const openTabs = state.openTabs.filter((p) => p !== path);
            const wasActive = state.activeFile === path;
            const fallback = openTabs.length > 0 ? openTabs[openTabs.length - 1] : null;
            return {
              openTabs,
              activeFile: wasActive ? (fallback ?? null) : state.activeFile,
            };
          });
        },
        setActiveFile(path) {
          set({ activeFile: path });
        },
        setActiveFileContent(path, content) {
          set((state) => ({
            fileBuffers: { ...state.fileBuffers, [path]: content },
          }));
        },
        pushAgentTurn(turn) {
          set((state) => ({
            agentTurns: replaceOrAppendByTurnIndex(state.agentTurns, turn),
          }));
        },
        pushEvent(event) {
          set((state) => {
            // Deduplicate by `id` and keep the stream sorted ascending so
            // the Timeline + grader-replay reads consistently.
            if (state.events.some((e) => e.id === event.id)) {
              return {};
            }
            const next = insertSorted(state.events, event);
            // Side-effects when a session-graded submission lands:
            // evict this store from the LRU so we don't keep its (now
            // immutable) state in memory forever.
            if (event.event_type === "submission.graded") {
              queueMicrotask(() => evictStore(sessionId));
            }
            return {
              events: next,
              lastEventId: Math.max(state.lastEventId, event.id),
            };
          });
        },
        setSandboxDriver(driver) {
          set({ sandboxDriver: driver });
        },
        setCommandPaletteOpen(open) {
          set({ commandPaletteOpen: open });
        },
        setSearchPanelOpen(open) {
          set({ searchPanelOpen: open });
        },
        setHelpOverlayOpen(open) {
          set({ helpOverlayOpen: open });
        },
        setActiveLineFocus(line) {
          set({ activeLineFocus: line });
        },
        setScratchpadOpen(open, trigger) {
          // Item 29 — stash the trigger so the pane's open-transition
          // effect can pull it on the next render. We only update the
          // trigger when one was explicitly supplied; an undefined
          // trigger preserves the previous value so a `setOpen(false)`
          // call that doesn't pass a trigger doesn't wipe the value
          // before the open-transition effect has a chance to read it.
          if (trigger !== undefined) {
            set({ scratchpadOpen: open, lastScratchpadTrigger: trigger });
          } else {
            set({ scratchpadOpen: open });
          }
        },
        setScratchpadBytes(bytes) {
          // Clamp to a non-negative integer so a malformed caller can't
          // poison the "viewed during prompt" guard. ``Number.isFinite``
          // catches NaN; ``Math.max`` floors any accidental negative.
          const next = Number.isFinite(bytes) ? Math.max(0, Math.floor(bytes)) : 0;
          set({ scratchpadBytes: next });
        },
        setActivePath(path, line = null) {
          set((state) => ({
            openTabs: state.openTabs.includes(path)
              ? state.openTabs
              : [...state.openTabs, path],
            activeFile: path,
            activeLineFocus: line,
          }));
        },
        reset() {
          set({
            selectedContext: [],
            openTabs: [],
            activeFile: null,
            fileBuffers: {},
            agentTurns: [],
            events: [],
            lastEventId: 0,
            commandPaletteOpen: false,
            searchPanelOpen: false,
            helpOverlayOpen: false,
            activeLineFocus: null,
            // Scratchpad: ``scratchpadOpen`` is a persisted user preference
            // — leave it alone here so a full ``reset()`` doesn't surprise
            // a returning user with a collapsed pane. ``scratchpadBytes``
            // is just a runtime cache; clearing it is safe.
            scratchpadBytes: 0,
            lastScratchpadTrigger: null,
          });
        },
        resetForWorkspaceReset() {
          // P0-12 — surgical clear: drop file buffers + open tabs (they
          // reference paths that may no longer exist post-reset) and the
          // active file selection. PRESERVE selectedContext, events,
          // agentTurns, lastEventId. The store keeps reading new
          // events (including the just-emitted ``session.reset``) via
          // the WS stream.
          set({
            openTabs: [],
            activeFile: null,
            fileBuffers: {},
          });
        },
      }),
      {
        name: persistName,
        storage: createJSONStorage(() => localStorage),
        // Don't persist the full event stream — it's reloadable from the server.
        partialize: (state) => ({
          sessionId: state.sessionId,
          selectedContext: state.selectedContext,
          openTabs: state.openTabs,
          activeFile: state.activeFile,
          fileBuffers: state.fileBuffers,
          // P1-4 — persist the scratchpad open/closed preference so the
          // user's choice survives reloads. The body itself lives
          // server-side; we deliberately don't persist scratchpadBytes
          // because it's reloadable on mount and would otherwise lie
          // about a session whose note was cleared in another tab.
          scratchpadOpen: state.scratchpadOpen,
        }),
        version: 1,
      }
    )
  );
}

/**
 * Get (or create) the persisted store for a session id.
 *
 * ``userId`` (optional) scopes the localStorage key so a user switch on
 * the same browser doesn't reuse the previous account's slice. Callers
 * that don't yet have the authenticated id (e.g. tests, the brief
 * unauth bootstrap window) omit it and inherit the ``"anon"`` default —
 * once the real id arrives the next call returns a freshly-keyed store.
 */
export function useWorkspaceStore(
  sessionId: string,
  userId?: string | null,
): StoreFactory {
  const key = storeKey(userId, sessionId);
  let existing = stores.get(key);
  if (!existing) {
    // Bound the factory cache. Map preserves insertion order so the first
    // key is the least-recently-created — a reasonable LRU proxy.
    while (stores.size >= MAX_STORES) {
      const firstKey = stores.keys().next().value;
      if (firstKey === undefined) break;
      stores.delete(firstKey);
    }
    existing = makeWorkspaceStore(sessionId, userId);
    stores.set(key, existing);
  } else {
    // Touch ordering so this session moves to the tail of the LRU.
    stores.delete(key);
    stores.set(key, existing);
  }
  return existing;
}

/**
 * Drop a store factory once its session is fully graded. Exported for tests.
 *
 * ``userId`` is optional for backward compat — when omitted we evict
 * every cached entry whose tail matches the session id (covers the
 * un-authed bootstrap and the eventual signed-in mount in one go).
 */
export function evictStore(sessionId: string, userId?: string | null): void {
  if (userId !== undefined) {
    stores.delete(storeKey(userId, sessionId));
    return;
  }
  for (const k of Array.from(stores.keys())) {
    if (k.endsWith(`:${sessionId}`)) stores.delete(k);
  }
}

function dedupe<T>(arr: T[]): T[] {
  return Array.from(new Set(arr));
}

function insertSorted(
  events: SupervisionEvent[],
  next: SupervisionEvent
): SupervisionEvent[] {
  if (events.length === 0 || events[events.length - 1]!.id < next.id) {
    return [...events, next];
  }
  const copy = events.slice();
  let i = copy.length;
  while (i > 0 && copy[i - 1]!.id > next.id) i -= 1;
  copy.splice(i, 0, next);
  return copy;
}

function replaceOrAppendByTurnIndex(
  turns: AgentTurn[],
  next: AgentTurn
): AgentTurn[] {
  const idx = turns.findIndex((t) => t.turn_index === next.turn_index);
  if (idx === -1) return [...turns, next];
  const copy = turns.slice();
  copy[idx] = next;
  return copy;
}
