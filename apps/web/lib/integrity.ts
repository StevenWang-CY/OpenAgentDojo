/**
 * P0-8 — proctored-mode integrity signaller.
 *
 * Wires window/document listeners that observe focus + paste events while
 * a proctored session is open in the workspace. Each accepted event POSTs
 * to ``/api/v1/sessions/{id}/events/integrity``; the backend persists it
 * (only when ``session.mode === 'proctored'``) and bumps the rolling
 * ``sessions.integrity_signals_count``.
 *
 * Behaviour:
 *   * Honor mode (``mode !== 'proctored'``): the signaller is a no-op.
 *     ``new IntegritySignaller(...).start()`` returns without attaching a
 *     single listener so the user experiences no surveillance instrumentation.
 *   * Proctored mode: ``window.blur``, ``window.focus``,
 *     ``document.visibilitychange``, ``document.paste``, and
 *     ``document.contextmenu`` are observed. The emitter is shared so a
 *     single render mounts the listener set once; ``dispose()`` undoes
 *     everything.
 *   * Each kind is debounced to at most one event per 500ms so a rapid
 *     visibility flap (e.g. macOS hover-to-preview) doesn't carpet-bomb
 *     the audit log.
 *   * Network failures are swallowed silently — the listener should not
 *     break the workspace if the integrity endpoint is briefly unreachable.
 *     A returned 409 (rare; the backend already drops self-study) is
 *     also a no-op.
 */

import { postIntegrityEvent } from "@/lib/api";

export type IntegrityKind =
  | "tab.blurred"
  | "tab.focused"
  | "paste.large"
  | "focus.lost"
  | "proctored.violation";

/** Threshold above which a paste counts as "large" — the FE drops shorter
 *  pastes entirely so we don't emit on every single-line copy/paste. */
export const PASTE_LARGE_THRESHOLD_CHARS = 200;

/** Per-kind debounce window in ms. A second event of the same kind inside
 *  this window is dropped — the FE never spams the backend with focus
 *  flaps. The backend bucket (60/min/session) is the second line of
 *  defence. */
const DEBOUNCE_MS = 500;

export interface IntegritySignallerOptions {
  /** Session UUID — used to address the integrity endpoint. */
  sessionId: string;
  /** Posture of the session. ``"proctored"`` attaches listeners; anything
   *  else short-circuits to a no-op signaller. */
  mode: string;
  /** Posts the event to the backend. Injectable so tests can capture the
   *  call set without touching ``fetch``. Defaults to the real
   *  ``postIntegrityEvent``. */
  post?: (
    sessionId: string,
    kind: IntegrityKind,
    payload: Record<string, unknown>,
  ) => Promise<void>;
}

export class IntegritySignaller {
  private readonly sessionId: string;
  private readonly enabled: boolean;
  private readonly post: NonNullable<IntegritySignallerOptions["post"]>;
  private disposed = false;
  // ``Map<kind, last-emit-ms>`` for the debounce.
  private readonly lastEmit = new Map<IntegrityKind, number>();
  // Wall-clock when the tab last became visible/blurred so we can report
  // the duration in the event payload.
  private lastVisibleAt: number;
  private lastBlurredAt: number | null = null;
  // Bound listeners so we can remove them in ``dispose``.
  private readonly handlers: Array<{
    target: EventTarget;
    type: string;
    fn: EventListener;
  }> = [];

  constructor(opts: IntegritySignallerOptions) {
    this.sessionId = opts.sessionId;
    this.enabled = opts.mode === "proctored";
    this.post = opts.post ?? postIntegrityEvent;
    this.lastVisibleAt = typeof performance !== "undefined" ? performance.now() : Date.now();
  }

  /** Attach the listeners. Returns ``this`` for chaining. No-op when
   *  ``mode !== 'proctored'``. */
  start(): this {
    if (!this.enabled) return this;
    if (typeof window === "undefined" || typeof document === "undefined") {
      // SSR safety — the FE only constructs this inside useEffect, so the
      // guard is defence-in-depth.
      return this;
    }

    const onBlur = () => {
      const now = this.now();
      const seconds = Math.max(0, Math.floor((now - this.lastVisibleAt) / 1000));
      this.lastBlurredAt = now;
      void this.emit("tab.blurred", { seconds_visible_before: seconds });
    };
    const onFocus = () => {
      const now = this.now();
      const seconds =
        this.lastBlurredAt !== null
          ? Math.max(0, Math.floor((now - this.lastBlurredAt) / 1000))
          : 0;
      this.lastVisibleAt = now;
      this.lastBlurredAt = null;
      void this.emit("tab.focused", { seconds_blurred: seconds });
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        onBlur();
      } else if (document.visibilityState === "visible") {
        onFocus();
      }
    };
    const onPaste = (ev: Event) => {
      const ce = ev as ClipboardEvent;
      const text = ce.clipboardData?.getData("text") ?? "";
      if (text.length <= PASTE_LARGE_THRESHOLD_CHARS) return;
      const target = inferPasteTarget(ce.target);
      void this.emit("paste.large", {
        chars: text.length,
        target,
      });
    };
    const onContextMenu = (ev: Event) => {
      // Only intercept right-clicks that land inside a documented
      // paste-target zone (editor, agent_chat, terminal). Right-clicks
      // on the surrounding application chrome — toolbar buttons,
      // sidebar navigation, header — must remain no-ops so accessibility
      // affordances (custom context menus on links, spellcheck on form
      // inputs the user owns) keep working.
      const target = ev.target;
      if (
        !(target instanceof HTMLElement) ||
        target.closest("[data-paste-target]") === null
      ) {
        return;
      }
      ev.preventDefault();
      const surface = inferPasteTarget(target);
      void this.emit("proctored.violation", {
        kind: "context_menu",
        detail: "browser context menu blocked",
        target: surface,
      });
    };

    this.attach(window, "blur", onBlur);
    this.attach(window, "focus", onFocus);
    this.attach(document, "visibilitychange", onVisibility);
    this.attach(document, "paste", onPaste);
    this.attach(document, "contextmenu", onContextMenu);

    return this;
  }

  /** Remove every attached listener. Idempotent. */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const { target, type, fn } of this.handlers) {
      try {
        target.removeEventListener(type, fn);
      } catch {
        // Defensive: a detached node can no-op a remove.
      }
    }
    this.handlers.length = 0;
  }

  private attach(target: EventTarget, type: string, fn: EventListener): void {
    target.addEventListener(type, fn);
    this.handlers.push({ target, type, fn });
  }

  private now(): number {
    return typeof performance !== "undefined" ? performance.now() : Date.now();
  }

  /** Emit ``kind`` with ``payload`` if the per-kind debounce allows it.
   *  Public so tests can drive emissions deterministically without
   *  fabricating browser events. */
  async emit(
    kind: IntegrityKind,
    payload: Record<string, unknown>,
  ): Promise<void> {
    if (this.disposed) return;
    const now = this.now();
    const last = this.lastEmit.get(kind) ?? 0;
    if (now - last < DEBOUNCE_MS) {
      return;
    }
    this.lastEmit.set(kind, now);
    try {
      await this.post(this.sessionId, kind, payload);
    } catch {
      // Best-effort: the integrity surface is supplementary, never a
      // hard dependency of the workspace.
    }
  }
}

/** Walk up from ``target`` to the nearest ``[data-paste-target]`` ancestor
 *  and return the documented surface name. Defaults to ``"other"`` when no
 *  ancestor declares the attribute. */
export function inferPasteTarget(target: EventTarget | null): "agent_chat" | "editor" | "terminal" | "other" {
  if (target === null || !(target instanceof Element)) return "other";
  const closest = target.closest("[data-paste-target]") as HTMLElement | null;
  if (closest === null) return "other";
  const raw = closest.dataset.pasteTarget ?? "";
  if (raw === "agent_chat" || raw === "editor" || raw === "terminal") {
    return raw;
  }
  return "other";
}
