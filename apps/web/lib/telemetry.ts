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
  | "lsp_error"
  // P1-5 — fires when the synchronised-scroll hook actually scrolls
  // the partner pane (one pane scrolled, the other followed). Debounced
  // to once per second per ``direction`` so a long drag emits one event
  // rather than burning the funnel. Properties:
  //   - ``direction`` : ``"user_to_ideal"`` | ``"ideal_to_user"``
  //   - ``anchor_count``: number of anchor pairs in the anchor map
  //     (a lightweight indicator of how well-paired the two diffs are)
  | "three_way_diff_synced_scroll"
  // P1-5 — fires once per ``(event_id, side)`` hover/focus on the
  // load-bearing-line marker. Properties:
  //   - ``event_id`` : the supervision-event id the moment is pinned to
  //   - ``side``     : ``"user"`` | ``"ideal"``
  //   - ``moment_count``: how many moments collapse onto this line
  //     (>= 1; > 1 means the marker is the aggregated chip)
  | "load_bearing_line_hovered"
  // P1-6 — fires when the user clicks "Download replay (JSON)" or
  // "Download replay (ZIP)" in the report share dropdown / account
  // Data tab. Properties:
  //   - ``submission_id`` : target submission id
  //   - ``kind``          : ``"json"`` | ``"zip"``
  // The backend mirrors this with the prometheus counter
  // ``replay_export_requests_total{kind}`` so dashboards can join the
  // FE intent funnel against the BE delivery counter.
  | "replay_export_requested"
  // P1-6 — fires once the replay download lands (the browser-side
  // fetch resolved 200 and either parsed the JSON or triggered the
  // file save dialog). Properties:
  //   - ``submission_id`` : target submission id
  //   - ``kind``          : ``"json"`` | ``"zip"``
  //   - ``bytes``         : number — wire-size of the served body
  | "replay_export_succeeded"
  // P1-6 — fires when the replay request fails: a 404 (auth matrix
  // rejected the caller), a 503 (verify secret unavailable), a
  // network error, or a JSON parse failure on the JSON variant.
  // Properties:
  //   - ``submission_id`` : target submission id
  //   - ``kind``          : ``"json"`` | ``"zip"``
  //   - ``error_class``   : a short discriminator the operator can
  //     bucket on (``http_404`` / ``http_503`` / ``network_error`` /
  //     ``parse_error`` / ``unknown``)
  | "replay_export_failed"
  // P1-4 (§C "Telemetry rollup") — fires the first time the
  // scratchpad pane is rendered visible during a session AND on
  // every subsequent toggle from collapsed → expanded. Wired to the
  // ``workspaceStore.scratchpadOpen`` setter so the FE never has to
  // duplicate the toggle logic across keybind + button surfaces.
  // Properties:
  //   - ``session_id`` : the owning session UUID (string)
  //   - ``trigger``    : ``"button"`` | ``"keybind"`` | ``"deeplink"`` —
  //     how the user opened it; lets the funnel split "people who
  //     discover the pane via the button" from "people who already
  //     knew about it and used ⌘\\".
  | "scratchpad_opened"
  // P1-4 (§C) — fires after ``PUT /sessions/{id}/note`` returns 200.
  // Mirror of the BE-side ``note.edited`` supervision event but kept
  // in the FE telemetry stream so the analytics funnel (PostHog) can
  // see edit cadence without joining against ``supervision_events``.
  // Properties:
  //   - ``session_id``  : the owning session UUID (string)
  //   - ``bytes``       : new body length in UTF-8 bytes
  //   - ``debounced_ms``: how long the 1.5 s autosave debounce sat
  //     before flushing (often 1500; lower on a final blur-flush)
  | "scratchpad_edit_persisted"
  // P1-4 (§C) — FE mirror of the BE supervision event
  // ``note.viewed_during_prompt``. Fires when the agent-chat
  // composer focuses while the scratchpad has > 0 bytes. The BE
  // event is the canonical source of truth for the post-mortem
  // timeline; this FE copy exists so the cookie-consent-gated
  // analytics funnel can see the same signal without a BE join.
  // Properties:
  //   - ``session_id``   : the owning session UUID (string)
  //   - ``bytes_at_view``: byte length of the scratchpad body at the
  //     moment the composer focused (matches the BE payload field)
  | "scratchpad_viewed_during_prompt"
  // P1-4 (§C, coaching reflection telemetry) — fires once when the
  // post-mortem "// what you wrote vs. what you did" section enters
  // the viewport (IntersectionObserver, 50% threshold). Lazy-load
  // bookkeeping: the FE only requests
  // ``GET /submissions/{id}/coaching`` after this event fires, so
  // its count is also the cold-cache request count.
  // Properties:
  //   - ``submission_id`` : target submission UUID (string)
  //   - ``cached``        : boolean — true when the BE returned a
  //     cache-hit, false when the LLM was invoked. Lets the funnel
  //     bucket "first viewer" vs "cached reflection" separately.
  | "coaching_reflection_shown"
  // P1-4 (§C) — fires when the user clicks an anchor inside the
  // coaching section: a timeline event quote or a scratchpad note
  // quote. Lets us tell whether the cross-surface anchoring is
  // actually used (cheap signal for the "is the coaching pulling
  // its weight" question).
  // Properties:
  //   - ``submission_id`` : target submission UUID (string)
  //   - ``anchor_kind``   : ``"timeline"`` | ``"note_quote"`` —
  //     which side of the reflection the user clicked
  //   - ``event_id``      : present only when ``anchor_kind ===
  //     "timeline"``; the supervision-event id the anchor jumps to
  | "coaching_reflection_anchor_clicked"
  // P1-4 (audit Item 19) — fires when the coaching reflection
  // ``useQuery`` lands in an error state. The user-facing behaviour
  // is unchanged (the section is silently hidden) but we want
  // oncall observability so a stale BE deploy can be triaged from
  // the analytics funnel without a log dive. Properties:
  //   - ``status`` : HTTP status (0 for network errors, otherwise
  //     the BE response status — typically 500 / 503 / 504)
  | "coaching_reflection_failed"
  // P1-4 (audit Item A3) — fires when the ``note.viewed_during_prompt``
  // best-effort BE POST fails. The composer focus telemetry still
  // landed in PostHog, so this event exists purely so the operator
  // can spot a degraded BE write path. Properties:
  //   - ``status`` : HTTP status (0 for network, otherwise BE status)
  | "scratchpad_viewed_during_prompt_failed";

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
  three_way_diff_synced_scroll: "three_way_diff_synced_scroll",
  load_bearing_line_hovered: "load_bearing_line_hovered",
  replay_export_requested: "replay_export_requested",
  replay_export_succeeded: "replay_export_succeeded",
  replay_export_failed: "replay_export_failed",
  scratchpad_opened: "scratchpad_opened",
  scratchpad_edit_persisted: "scratchpad_edit_persisted",
  scratchpad_viewed_during_prompt: "scratchpad_viewed_during_prompt",
  coaching_reflection_shown: "coaching_reflection_shown",
  coaching_reflection_anchor_clicked: "coaching_reflection_anchor_clicked",
  coaching_reflection_failed: "coaching_reflection_failed",
  scratchpad_viewed_during_prompt_failed:
    "scratchpad_viewed_during_prompt_failed",
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
