/**
 * Provider-neutral telemetry hook.
 *
 * Behaviour:
 *   - Default (no env vars): no-op. We never phone home and never load an SDK.
 *     Events still land in an in-memory ring buffer at
 *     `window.__arenaTelemetry` so dev/QA can inspect them in DevTools.
 *   - When `NEXT_PUBLIC_POSTHOG_KEY` is set (see `TelemetryProvider`), the
 *     provider lazily imports `posthog-js` and wires it in. The same
 *     `track`/`identify`/`pageView` functions then forward to PostHog.
 *   - SSR-safe: every public function short-circuits when `window` is absent.
 *
 * Payloads MUST stay small and PII-free. Specifically: never include prompt
 * text, file contents, emails, or auth tokens. The event allowlist below is
 * deliberately narrow.
 */

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
  | "sign_in_completed";

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
} as const satisfies Record<TelemetryEvent, TelemetryEvent>;

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

  if (posthog) {
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

  if (posthog) {
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

  if (posthog) {
    try {
      posthog.capture("$pageview", { $current_url: path });
    } catch {
      // Silent.
    }
  }
}
