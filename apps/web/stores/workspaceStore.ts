"use client";

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { AgentTurn, SupervisionEvent } from "@arena/shared-types";

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
 */
const MAX_STORES = 16;
const stores = new Map<string, StoreFactory>();

function makeWorkspaceStore(sessionId: string) {
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
        reset() {
          set({
            selectedContext: [],
            openTabs: [],
            activeFile: null,
            fileBuffers: {},
            agentTurns: [],
            events: [],
            lastEventId: 0,
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
        name: `arena:workspace:${sessionId}`,
        storage: createJSONStorage(() => localStorage),
        // Don't persist the full event stream — it's reloadable from the server.
        partialize: (state) => ({
          sessionId: state.sessionId,
          selectedContext: state.selectedContext,
          openTabs: state.openTabs,
          activeFile: state.activeFile,
          fileBuffers: state.fileBuffers,
        }),
        version: 1,
      }
    )
  );
}

/** Get (or create) the persisted store for a session id. */
export function useWorkspaceStore(sessionId: string): StoreFactory {
  let existing = stores.get(sessionId);
  if (!existing) {
    // Bound the factory cache. Map preserves insertion order so the first
    // key is the least-recently-created — a reasonable LRU proxy.
    while (stores.size >= MAX_STORES) {
      const firstKey = stores.keys().next().value;
      if (firstKey === undefined) break;
      stores.delete(firstKey);
    }
    existing = makeWorkspaceStore(sessionId);
    stores.set(sessionId, existing);
  } else {
    // Touch ordering so this session moves to the tail of the LRU.
    stores.delete(sessionId);
    stores.set(sessionId, existing);
  }
  return existing;
}

/** Drop a store factory once its session is fully graded. Exported for tests. */
export function evictStore(sessionId: string): void {
  stores.delete(sessionId);
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
