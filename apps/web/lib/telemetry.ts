/**
 * Provider-neutral telemetry hook.
 *
 * Behaviour:
 *   - Default (no env vars): no-op. We never phone home and never load an SDK.
 *     Events still land in an in-memory ring buffer at
 *     `window.__arenaTelemetry` so dev/QA can inspect them in DevTools.
 *   - When `NEXT_PUBLIC_POSTHOG_KEY` is set (see `TelemetryProvider`), the
 *     provider lazily imports `posthog-js` and wires it in — but ONLY after
 *     the user has explicitly granted analytics consent (P0-5). Until then,
 *     ``__setTelemetryClient`` is never called and the in-memory ring
 *     buffer is the only sink. The provider listens for the
 *     ``consent-changed`` custom event so opt-ins take effect immediately.
 *   - SSR-safe: every public function short-circuits when `window` is absent.
 *
 * Payloads MUST stay small and PII-free. Specifically: never include prompt
 * text, file contents, emails, or auth tokens. The event allowlist below is
 * deliberately narrow.
 *
 * Consent gate
 * ------------
 * Every emitting function (``track``/``identify``/``pageView``) checks
 * ``getConsent().analytics === true`` before forwarding to PostHog. The ring
 * buffer remains populated regardless so DevTools introspection still works
 * — it's an in-memory dev affordance, not a remote sink.
 */
import { getConsent } from "./consent";

// ── Public event vocabulary ─────────────────────────────────────────────────

export type TelemetryEvent =
  | "mission_viewed"
  | "mission_started"
  | "prompt_submitted"
  | "patch_applied"
  | "submission_started"
  | "report_viewed"
  | "report_shared"
  | "profile_viewed"
  | "sign_in_requested"
  | "sign_in_completed"
  // P0-3 — emitted by the Retry CTA on the report page. Properties:
  // ``mission_id``, ``previous_session_id``.
  | "mission_retried"
  // P0-4 — emitted by the GiveUpDialog confirm action. Property:
  // ``session_id``. Fires BEFORE the API call so the abandon signal is
  // recorded even if the network roundtrip fails.
  | "session_gave_up"
  // P0-12 — emitted by the workspace overflow menu when the user opens
  // the Reset confirmation dialog. Properties: ``session_id``. Distinct
  // from ``session_reset_completed`` (which fires after the API succeeds)
  // so the drop-off between "intent" and "confirmed reset" is visible.
  | "session_reset_requested"
  // P0-12 — emitted after ``POST /sessions/{id}/reset`` returns 200.
  // Properties: ``session_id``, ``reset_count``, ``files_reset``.
  | "session_reset_completed"
  // P0-11 — emitted by the report dropdown when the user requests a
  // PDF / PNG. Properties: ``kind``, ``cache_hit`` (boolean — true when
  // the cached row is already ready, false when the worker has to run).
  | "report_render_requested"
  // P0-11 — emitted when a render becomes ready (either cache-hit on the
  // first call or after polling). Properties: ``submission_id``, ``kind``,
  // ``ms_to_ready`` (number — wall-clock ms from the user's click to the
  // ready signal; ~0 on a cache hit).
  | "report_render_succeeded"
  // P0-11 — emitted when a render gives up: worker reported ``failed``,
  // a network error during polling, or the 24-attempt (2 min) ceiling
  // was reached. Properties: ``submission_id``, ``kind``, ``error_class``
  // (one of ``render_failed`` | ``polling_timeout`` | ``network_error``).
  | "report_render_failed"
  // P0-11 — emitted by the public /verify page on first paint.
  // Properties: ``submission_id``, ``referer_host`` (when available).
  | "report_verified"
  // P0-10 — emitted when the user clicks "Resend link" on the sign-in
  // post-send card. No properties (the event is intentionally
  // PII-free; the throttle outcome lives on the API metric, not the
  // FE telemetry stream).
  | "magic_link_resend_clicked"
  // P0-10 — emitted when the user clicks the "Continue with GitHub"
  // CTA on the sign-in post-send card (the OAuth fallback path).
  // Distinct from the sign-in start telemetry so the funnel can
  // distinguish "tried magic link, fell back to GitHub" from
  // "started with GitHub directly".
  | "github_oauth_clicked_from_resend"
  // P0-7 — emitted by the sign-in page when the user clicks the
  // top-level "Continue with GitHub" CTA (above the magic-link form).
  // Distinct from ``github_oauth_clicked_from_resend`` so the funnel
  // can distinguish "started with GitHub" from "fell back to GitHub
  // after the email didn't arrive".
  | "sign_in_github_clicked"
  // P1-2 — emitted when an adaptive next-mission recommendation surface
  // mounts with data. ``kind`` discriminates the three surfaces
  // (profile strip, catalog chip, report footer); ``weakest_dim`` is
  // ``null`` for cold-start users (ladder path); ``mission_ids`` is the
  // ordered top-3 (or just the single id for the catalog chip / report
  // footer). ``signed_in`` is technically redundant (the surface only
  // renders for signed-in viewers) but kept for the funnel join.
  | "recommendation_shown"
  // P1-2 — emitted when the user clicks a recommendation card / chip.
  // ``position`` is the 0-indexed slot in the rendered list (always 0
  // for the catalog chip / report footer); ``mission_id`` lets the
  // attribution join across surfaces; ``kind`` matches
  // ``recommendation_shown``'s kind.
  | "recommendation_clicked"
  // P1-3 — emitted by ``lib/lsp/client.ts`` once the JSON-RPC
  // ``initialize`` handshake against the in-sandbox language server
  // succeeds. ``cold_start_ms`` is the wall-clock duration from WS
  // open to the ``initialized`` notification being sent — this is the
  // load-bearing operational metric (the design caps it at 5 s for
  // the amber chip, beyond which we want telemetry to surface a
  // suspicious cold start).
  | "lsp_session_started"
  // P1-3 — emitted by ``CodeEditor`` when the user accepts a
  // completion suggestion proxied through the LSP client. Monaco
  // doesn't fire a direct "completion accepted" event; we hook the
  // ``IStandaloneCodeEditor.onDidPaste`` / ``onDidCompositionEnd``
  // surface plus the completion provider's ``resolveCompletionItem``
  // — when both fire in quick succession we count an acceptance.
  // Best-effort signal; missing one acceptance is fine.
  | "lsp_completion_accepted"
  // P1-3 — emitted on any LSP failure path: structured ``lsp_error``
  // text frame from the backend (``binary_not_found``,
  // ``unsupported_language``, ...), an unexpected WS close
  // (``close_4404`` / ``close_4503`` / ``close_1011`` / ...), or a
  // local ``initialize_failed``. ``error_class`` is a free-form
  // string so a future backend error class doesn't require a FE
  // typedef bump.
  | "lsp_error";

/**
 * Canonical event names, exposed as a const enum-like object so call sites
 * can write `track(TelemetryEvents.mission_viewed, …)` instead of a string
 * literal. Both forms type-check identically; we just like the autocomplete.
 */
export const TelemetryEvents = {
  mission_viewed: "mission_viewed",
  mission_started: "mission_started",
  prompt_submitted: "prompt_submitted",
  patch_applied: "patch_applied",
  submission_started: "submission_started",
  report_viewed: "report_viewed",
  report_shared: "report_shared",
  profile_viewed: "profile_viewed",
  sign_in_requested: "sign_in_requested",
  sign_in_completed: "sign_in_completed",
  mission_retried: "mission_retried",
  session_gave_up: "session_gave_up",
  session_reset_requested: "session_reset_requested",
  session_reset_completed: "session_reset_completed",
  report_render_requested: "report_render_requested",
  report_render_succeeded: "report_render_succeeded",
  report_render_failed: "report_render_failed",
  report_verified: "report_verified",
  magic_link_resend_clicked: "magic_link_resend_clicked",
  github_oauth_clicked_from_resend: "github_oauth_clicked_from_resend",
  sign_in_github_clicked: "sign_in_github_clicked",
  recommendation_shown: "recommendation_shown",
  recommendation_clicked: "recommendation_clicked",
  lsp_session_started: "lsp_session_started",
  lsp_completion_accepted: "lsp_completion_accepted",
  lsp_error: "lsp_error",
} as const satisfies Record<TelemetryEvent, TelemetryEvent>;

// ── P1-3 LSP convenience helpers ────────────────────────────────────────────

/**
 * Lightweight typed wrapper around ``track`` for the three LSP events.
 * Lives here (not in ``lib/lsp/client.ts``) so the consent gate stays a
 * single chokepoint and so the LSP client can be imported without
 * pulling the full telemetry surface into its own type graph.
 */
export type LspTelemetryEvent =
  | "lsp_session_started"
  | "lsp_completion_accepted"
  | "lsp_error";

export function trackLspEvent(
  event: LspTelemetryEvent,
  props: Record<string, unknown>
): void {
  track(event, props);
}

// ── Ring buffer & internal state ────────────────────────────────────────────

interface RecordedEvent {
  event: TelemetryEvent | "page_view" | "identify";
  props?: Record<string, unknown>;
  at: number; // epoch ms
}

const RING_BUFFER_SIZE = 100;

interface ArenaTelemetryGlobal {
  events: RecordedEvent[];
  dump: () => RecordedEvent[];
  clear: () => void;
}

declare global {
  // `var` is required to augment the global scope; `let`/`const` don't.
  var __arenaTelemetry: ArenaTelemetryGlobal | undefined;
}

function ringBuffer(): ArenaTelemetryGlobal {
  if (typeof window === "undefined") {
    // Returned but never actually mutated in SSR.
    return { events: [], dump: () => [], clear: () => undefined };
  }
  if (!window.__arenaTelemetry) {
    const events: RecordedEvent[] = [];
    window.__arenaTelemetry = {
      events,
      dump: () => events.slice(),
      clear: () => {
        events.length = 0;
      },
    };
  }
  return window.__arenaTelemetry;
}

/**
 * Reference to a PostHog-like client installed by `TelemetryProvider`.
 * Kept loose (no static `posthog-js` import) so the bundle does not pay for
 * the dep when it is absent.
 */
interface PosthogLike {
  capture: (event: string, props?: Record<string, unknown>) => void;
  identify: (userId: string, traits?: Record<string, unknown>) => void;
  // PostHog uses `$pageview` and reads `$current_url` from props if provided.
  // Falling through to `capture("$pageview", …)` is equivalent.
}

let posthog: PosthogLike | null = null;

/** Called by `TelemetryProvider` after `posthog-js` has finished loading. */
export function __setTelemetryClient(client: PosthogLike | null): void {
  posthog = client;
}

// ── Debounce for duplicate events ────────────────────────────────────────────

const DEBOUNCE_MS = 1000;
const recentSignatures = new Map<string, number>();

function shouldDebounce(event: string, props?: Record<string, unknown>): boolean {
  // Cheap stable signature: name + JSON of props with sorted keys. We don't
  // care about deep ordering — these are tiny flat objects.
  let sig = event;
  if (props) {
    const keys = Object.keys(props).sort();
    sig =
      event +
      "|" +
      keys.map((k) => `${k}=${stringifyPrimitive(props[k])}`).join("&");
  }
  const now = Date.now();
  const last = recentSignatures.get(sig);
  if (last !== undefined && now - last < DEBOUNCE_MS) {
    return true;
  }
  recentSignatures.set(sig, now);
  // Sweep old entries periodically so the map can't grow unbounded.
  if (recentSignatures.size > 64) {
    for (const [k, ts] of recentSignatures) {
      if (now - ts > DEBOUNCE_MS * 4) recentSignatures.delete(k);
    }
  }
  return false;
}

function stringifyPrimitive(v: unknown): string {
  if (v === null || v === undefined) return String(v);
  const t = typeof v;
  if (t === "string" || t === "number" || t === "boolean") return String(v);
  // Objects/arrays are stringified shallowly for the signature only; this is
  // not the wire payload, just a debounce key.
  try {
    return JSON.stringify(v);
  } catch {
    return "[unserialisable]";
  }
}

// ── Public API ──────────────────────────────────────────────────────────────

/**
 * Fire a telemetry event. Duplicates of the same event+props within 1s are
 * dropped so noisy callers (e.g. accidental double clicks) don't spam.
 *
 * Always SSR-safe: no-ops on the server.
 */
export function track(
  event: TelemetryEvent,
  props?: Record<string, unknown>
): void {
  if (typeof window === "undefined") return;
  if (shouldDebounce(event, props)) return;

  const buffer = ringBuffer();
  buffer.events.push({ event, props, at: Date.now() });
  if (buffer.events.length > RING_BUFFER_SIZE) {
    buffer.events.splice(0, buffer.events.length - RING_BUFFER_SIZE);
  }

  if (posthog && getConsent().analytics?.granted === true) {
    try {
      posthog.capture(event, props);
    } catch {
      // Silent — telemetry must never break user flows.
    }
  }
}

/**
 * Identify the current user. PII-free: callers should pass a stable user id
 * (UUID), not an email. Traits should be analytics-friendly (e.g. handle,
 * created_at) and small.
 */
export function identify(
  userId: string,
  traits?: Record<string, unknown>
): void {
  if (typeof window === "undefined") return;

  const buffer = ringBuffer();
  buffer.events.push({
    event: "identify",
    props: { userId, ...(traits ?? {}) },
    at: Date.now(),
  });
  if (buffer.events.length > RING_BUFFER_SIZE) {
    buffer.events.splice(0, buffer.events.length - RING_BUFFER_SIZE);
  }

  if (posthog && getConsent().analytics?.granted === true) {
    try {
      posthog.identify(userId, traits);
    } catch {
      // Silent.
    }
  }
}

/**
 * Record a route change. Called by `TelemetryProvider` on `usePathname()`
 * change; manual calls are also fine.
 */
export function pageView(path: string): void {
  if (typeof window === "undefined") return;
  if (shouldDebounce("page_view", { path })) return;

  const buffer = ringBuffer();
  buffer.events.push({ event: "page_view", props: { path }, at: Date.now() });
  if (buffer.events.length > RING_BUFFER_SIZE) {
    buffer.events.splice(0, buffer.events.length - RING_BUFFER_SIZE);
  }

  if (posthog && getConsent().analytics?.granted === true) {
    try {
      posthog.capture("$pageview", { $current_url: path });
    } catch {
      // Silent.
    }
  }
}
