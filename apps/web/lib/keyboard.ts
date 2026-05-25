"use client";

import * as React from "react";

/**
 * Keyboard-shortcut helpers (P0-9).
 *
 * The workspace registers global shortcuts at the document level so Monaco's
 * own keymap doesn't swallow them. We use the capture phase deliberately so
 * the listener runs BEFORE Monaco's bubbling-phase handlers — without that,
 * a user who has the editor focused can't open quick-open or find-in-files
 * because Monaco's command palette would intercept Cmd+Shift+P, and Ctrl+F
 * inside the editor would scroll the editor instead of opening our panel.
 */

/** Detected on every shortcut press. */
export interface ShortcutEvent {
  /** The matched shortcut id (callers switch on this). */
  id: string;
  /** The underlying ``KeyboardEvent`` — call ``preventDefault`` if appropriate. */
  event: KeyboardEvent;
}

/** Cross-platform "meta" — Cmd on macOS, Ctrl elsewhere. */
function isMeta(event: KeyboardEvent): boolean {
  // ``metaKey`` is the macOS Cmd; ``ctrlKey`` is Ctrl on Linux/Windows. We
  // accept either — the spec ships the shortcuts as ``Cmd/Ctrl+P`` for
  // exactly this reason.
  return event.metaKey || event.ctrlKey;
}

/**
 * True when the keyboard event originated inside an editable surface.
 * The ``?`` help shortcut is the only shortcut that respects this — every
 * other workspace shortcut deliberately fires even when an input is focused
 * (the user expects Cmd+P to open the palette from anywhere).
 */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (target.isContentEditable) return true;
  // Monaco renders a hidden ``<textarea>`` for keyboard input; the above
  // branch already catches it. Defensive check for any future editor that
  // surfaces a non-textarea editable host.
  if (target.getAttribute("role") === "textbox") return true;
  return false;
}

/** The set of shortcuts the workspace listens for. */
export type ShortcutId =
  | "quick-open"
  | "find-in-files"
  | "help-overlay"
  | "escape";

interface UseWorkspaceShortcutsOpts {
  /** Map of shortcut id → handler. Missing entries are no-ops. */
  on?: Partial<Record<ShortcutId, (event: KeyboardEvent) => void>>;
  /** When false, no listener is registered (useful for SSR / unmount). */
  enabled?: boolean;
}

/**
 * Register the workspace keymap on ``document``. Returns nothing — the hook
 * tears down its own listener on unmount.
 *
 * Bound shortcuts:
 *   * ``Cmd/Ctrl+P``       — ``quick-open`` (file picker).
 *   * ``Cmd/Ctrl+Shift+F`` — ``find-in-files`` (search panel).
 *   * ``?``                — ``help-overlay`` (only when not typing).
 *   * ``Esc``              — ``escape`` (close active overlay).
 */
export function useWorkspaceShortcuts(opts: UseWorkspaceShortcutsOpts): void {
  const { on, enabled = true } = opts;
  // Stash the latest handlers in a ref so the document listener can read
  // them without re-mounting on every render. Without this, the parent's
  // ``onX`` props would force a teardown + re-register pair on every render
  // and the capture-phase listener would race Monaco's handlers.
  const handlersRef = React.useRef(on);
  React.useEffect(() => {
    handlersRef.current = on;
  }, [on]);

  React.useEffect(() => {
    if (!enabled || typeof document === "undefined") return;

    const handler = (event: KeyboardEvent) => {
      const handlers = handlersRef.current ?? {};
      // Cmd/Ctrl+Shift+F → find-in-files.
      // We check this BEFORE plain Cmd/Ctrl+P because the shift modifier
      // wouldn't otherwise distinguish the two.
      if (
        isMeta(event) &&
        event.shiftKey &&
        (event.key === "F" || event.key === "f")
      ) {
        const cb = handlers["find-in-files"];
        if (cb) {
          event.preventDefault();
          cb(event);
        }
        return;
      }
      // Cmd/Ctrl+P → quick-open. ``event.key === "p" | "P"`` covers both
      // Caps Lock states. Shift is allowed-but-not-required (Cmd+P with
      // shift is essentially the same intent for our purposes); a separate
      // Cmd+Shift+P binding would need its own branch above.
      if (
        isMeta(event) &&
        !event.shiftKey &&
        (event.key === "p" || event.key === "P")
      ) {
        const cb = handlers["quick-open"];
        if (cb) {
          event.preventDefault();
          cb(event);
        }
        return;
      }
      // ``?`` → help overlay. Only when the user isn't typing — we don't
      // want to steal a ``?`` they're trying to type into the agent prompt.
      // Note: ``?`` is shift+slash on US keyboards; the cross-layout-safe
      // check is ``event.key === "?"``.
      if (event.key === "?" && !isTypingTarget(event.target)) {
        const cb = handlers["help-overlay"];
        if (cb) {
          event.preventDefault();
          cb(event);
        }
        return;
      }
      // Esc → close the topmost overlay. The parent is responsible for
      // deciding which overlay closes — we just deliver the signal.
      if (event.key === "Escape") {
        const cb = handlers["escape"];
        if (cb) {
          cb(event);
        }
      }
    };

    document.addEventListener("keydown", handler, { capture: true });
    return () => {
      document.removeEventListener("keydown", handler, { capture: true });
    };
  }, [enabled]);
}
