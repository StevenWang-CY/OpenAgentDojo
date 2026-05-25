"use client";

import * as React from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";

const STORAGE_KEY = "oad.help.suppressOnStart";

export interface HelpOverlayProps {
  open: boolean;
  onOpenChange(open: boolean): void;
}

/**
 * The ``?`` help overlay (P0-9).
 *
 * Surfaces the workspace keymap and a short supervision-tips section.
 * Opens automatically on the user's first visit (until they tick "don't
 * show on startup"). The "don't show" preference is persisted to
 * ``localStorage`` under ``oad.help.suppressOnStart`` so it survives across
 * sessions and tabs.
 *
 * NOTE: The auto-open-on-mount logic lives in the parent (``WorkspaceShell``)
 * not here — keeping this component a pure presentational dialog so tests
 * can mount it without mocking localStorage.
 */
export function HelpOverlay({ open, onOpenChange }: HelpOverlayProps) {
  const [suppressOnStart, setSuppressOnStart] = React.useState(false);

  // Read the persisted preference on first mount.
  React.useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      setSuppressOnStart(window.localStorage.getItem(STORAGE_KEY) === "true");
    } catch {
      // Private-mode browsers / blocked storage — fall through with the
      // default (false). The user can still tick the box for the rest of
      // the session; it just won't persist.
    }
  }, []);

  const toggleSuppress = React.useCallback((value: boolean) => {
    setSuppressOnStart(value);
    if (typeof window === "undefined") return;
    try {
      if (value) {
        window.localStorage.setItem(STORAGE_KEY, "true");
      } else {
        window.localStorage.removeItem(STORAGE_KEY);
      }
    } catch {
      // ignore — best-effort persistence
    }
  }, []);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="help-overlay"
        className="max-w-xl"
        aria-label="Workspace keyboard shortcuts"
      >
        <DialogHeader>
          <DialogTitle>Workspace shortcuts</DialogTitle>
          <DialogDescription>
            Keyboard-first surfaces for navigating the sandbox and supervising
            the agent.
          </DialogDescription>
        </DialogHeader>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[var(--color-muted-foreground)]">
              <th className="py-1 font-medium">Shortcut</th>
              <th className="py-1 font-medium">Action</th>
            </tr>
          </thead>
          <tbody className="font-mono text-xs">
            <ShortcutRow keys="Cmd/Ctrl+P" action="Quick open file" />
            <ShortcutRow keys="Cmd/Ctrl+Shift+F" action="Find in files" />
            <ShortcutRow keys="Cmd/Ctrl+Enter" action="Submit prompt to agent" />
            <ShortcutRow keys="Cmd/Ctrl+S" action="Save current file" />
            <ShortcutRow keys="Esc" action="Close panel / cancel" />
            <ShortcutRow keys="?" action="Toggle this help" />
          </tbody>
        </table>
        <section
          aria-labelledby="help-overlay-supervision-tips"
          className="mt-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-3"
        >
          <h3
            id="help-overlay-supervision-tips"
            className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]"
          >
            Supervision tips
          </h3>
          <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm text-[var(--color-foreground)]">
            <li>Open the diff before applying — the grader credits review.</li>
            <li>Select context before prompting — narrow context wins.</li>
            <li>Verify your fix — re-run tests after the agent patches.</li>
          </ul>
        </section>
        <label className="mt-2 flex items-center gap-2 text-xs text-[var(--color-muted-foreground)]">
          <input
            type="checkbox"
            checked={suppressOnStart}
            onChange={(e) => toggleSuppress(e.target.checked)}
            data-testid="help-overlay-suppress"
            className="size-3.5 rounded border border-[var(--color-border)] accent-[var(--color-primary)]"
          />
          Don&rsquo;t show on startup
        </label>
      </DialogContent>
    </Dialog>
  );
}

interface ShortcutRowProps {
  keys: string;
  action: string;
}

function ShortcutRow({ keys, action }: ShortcutRowProps) {
  return (
    <tr className="border-t border-[var(--color-border)]">
      <td className="py-1 pr-3">
        <kbd className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-1.5 py-0.5">
          {keys}
        </kbd>
      </td>
      <td className="py-1 text-[var(--color-foreground)]">{action}</td>
    </tr>
  );
}

/**
 * Test-only helper exported for the workspace shell — checks whether the
 * help overlay should auto-open on this load. Returns ``true`` for first-
 * time users (no localStorage key set) and ``false`` when the user has
 * dismissed it once with the "don't show on startup" checkbox.
 */
export function shouldAutoOpenHelp(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== "true";
  } catch {
    return false;
  }
}
