"use client";

/**
 * Client-side consent state library (P0-5).
 *
 * Architecture
 * ------------
 * The browser holds the *authoritative* opt-in state in localStorage so the
 * cookie banner can render synchronously on first paint without waiting on
 * a network round-trip — there is no "loading" flicker before the banner
 * decides whether to show. The backend keeps an append-only audit trail
 * (one ``user_consents`` row per change) for the signed-in user; the
 * localStorage record and the latest row converge via ``setKind`` (which
 * writes both) and on first login transition (see
 * ``syncLocalConsentToServer``).
 *
 * Why localStorage instead of a real cookie:
 *   - ``consent_v`` is a single-page-app concern. The marketing footer never
 *     SSRs on a user-specific basis, so the cookie value would never reach
 *     the server in a useful way.
 *   - The HTTP request budget on first paint is precious; a real cookie
 *     would still need to be set by JS post-hydration (the server can't
 *     issue it before getting the user's consent), so we'd pay for a cookie
 *     header on every request with no benefit.
 *
 * Versioning
 * ----------
 * ``LATEST_CONSENT_VERSION`` mirrors the backend's
 * ``settings.consent_policy_version`` default (apps/api/app/config.py).
 * Bumping either side without the other re-shows the banner — that's the
 * intended behaviour when the policy text materially changes.
 */

import type { ConsentKind, ConsentRecord, ConsentState } from "@arena/shared-types";
import * as React from "react";
import { ApiError, auth } from "@/lib/api";

export type { ConsentKind, ConsentRecord, ConsentState };

/**
 * Mirror of ``settings.consent_policy_version`` (apps/api/app/config.py).
 * Bump when the privacy policy text changes in a way that materially affects
 * user expectations (new processor, new data category, expanded retention,
 * etc.). The cookie banner re-appears for everyone whose stored version is
 * lower than this value.
 */
export const LATEST_CONSENT_VERSION = 1;

/** Per-kind localStorage payload (a {@link ConsentState}). */
export const STORAGE_KEY = "consent_v";

/**
 * Stored policy version number — written alongside ``STORAGE_KEY`` so the
 * banner can short-circuit on version mismatch without parsing the full
 * state blob. The version is also embedded in each {@link ConsentRecord};
 * this key is just a fast-path index.
 */
export const STORAGE_VERSION_KEY = "consent_v_version";

/**
 * Cross-tab + same-tab notification channel. The ``storage`` event already
 * handles cross-tab sync, but it does not fire in the originating tab —
 * so {@link setConsent} dispatches this custom event on ``window`` to let
 * the {@link TelemetryProvider} react to a same-tab opt-in immediately.
 *
 * Detail shape: ``{ state: ConsentState }``.
 */
export const CONSENT_CHANGED_EVENT = "consent-changed";

const EMPTY_STATE: ConsentState = {
  analytics: null,
  functional: null,
  marketing: null,
};

function hasWindow(): boolean {
  return typeof window !== "undefined";
}

function readVersion(): number {
  if (!hasWindow()) return 0;
  const raw = window.localStorage.getItem(STORAGE_VERSION_KEY);
  if (!raw) return 0;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * Synchronous read of the stored consent state. SSR-safe (returns the empty
 * state on the server). Returns the empty state if nothing is stored OR if
 * the stored version is below {@link LATEST_CONSENT_VERSION} — the latter
 * forces the banner to re-show on a policy bump.
 */
export function getConsent(): ConsentState {
  if (!hasWindow()) return { ...EMPTY_STATE };
  if (readVersion() < LATEST_CONSENT_VERSION) return { ...EMPTY_STATE };

  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return { ...EMPTY_STATE };

  try {
    const parsed = JSON.parse(raw) as Partial<ConsentState> | null;
    if (!parsed || typeof parsed !== "object") return { ...EMPTY_STATE };
    return {
      analytics: normalizeRecord(parsed.analytics),
      functional: normalizeRecord(parsed.functional),
      marketing: normalizeRecord(parsed.marketing),
    };
  } catch {
    return { ...EMPTY_STATE };
  }
}

function normalizeRecord(value: unknown): ConsentRecord | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Partial<ConsentRecord>;
  if (
    typeof record.granted !== "boolean" ||
    typeof record.version !== "number" ||
    typeof record.at !== "string"
  ) {
    return null;
  }
  return {
    granted: record.granted,
    version: record.version,
    at: record.at,
  };
}

function writeState(state: ConsentState): void {
  if (!hasWindow()) return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  window.localStorage.setItem(
    STORAGE_VERSION_KEY,
    String(LATEST_CONSENT_VERSION),
  );
}

function dispatchChange(state: ConsentState): void {
  if (!hasWindow()) return;
  try {
    window.dispatchEvent(
      new CustomEvent<{ state: ConsentState }>(CONSENT_CHANGED_EVENT, {
        detail: { state },
      }),
    );
  } catch {
    // Older Safari throws when constructing CustomEvent in some contexts —
    // the storage event covers cross-tab anyway, so this is a soft path.
  }
}

/**
 * Write a single ``ConsentKind`` to localStorage and return the new record.
 * Dispatches a {@link CONSENT_CHANGED_EVENT} so same-tab subscribers (the
 * {@link TelemetryProvider}) can react immediately.
 *
 * SSR-safe: no-ops on the server and returns a synthetic record so callers
 * never have to branch.
 */
export function setConsent(kind: ConsentKind, granted: boolean): ConsentRecord {
  const record: ConsentRecord = {
    granted,
    version: LATEST_CONSENT_VERSION,
    at: new Date().toISOString(),
  };
  const next: ConsentState = { ...getConsent(), [kind]: record };
  writeState(next);
  dispatchChange(next);
  return record;
}

/**
 * Returns ``true`` when the cookie banner should render — i.e. it's a first
 * visit OR the stored policy version is older than the current one. The hook
 * inside {@link CookieConsentBanner} uses this; the function is exported so
 * tests can drive it without mounting the component.
 */
export function bannerShouldShow(): boolean {
  if (!hasWindow()) return false;
  if (readVersion() < LATEST_CONSENT_VERSION) return true;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  return raw === null;
}

/**
 * Apply the "Essential only" default — analytics and marketing rejected,
 * functional accepted. Used by the banner's middle button. Writes all three
 * kinds in a single localStorage update so the banner can dismiss without an
 * intermediate state where ``functional === null``.
 */
export function applyEssentialOnlyDefault(): void {
  const now = new Date().toISOString();
  const next: ConsentState = {
    analytics: { granted: false, version: LATEST_CONSENT_VERSION, at: now },
    functional: { granted: true, version: LATEST_CONSENT_VERSION, at: now },
    marketing: { granted: false, version: LATEST_CONSENT_VERSION, at: now },
  };
  writeState(next);
  dispatchChange(next);
}

/**
 * Bulk variant of {@link setConsent} used by the "Accept all" button and the
 * Customize dialog's save action. Writes once, dispatches once.
 */
export function setConsentBulk(
  updates: Partial<Record<ConsentKind, boolean>>,
): ConsentState {
  const now = new Date().toISOString();
  const current = getConsent();
  const next: ConsentState = { ...current };
  (Object.entries(updates) as [ConsentKind, boolean][]).forEach(([kind, granted]) => {
    next[kind] = { granted, version: LATEST_CONSENT_VERSION, at: now };
  });
  writeState(next);
  dispatchChange(next);
  return next;
}

/**
 * React hook surfacing the live consent state. On mount it reads
 * localStorage synchronously (no flicker) and subscribes to ``storage``
 * events (cross-tab) + the {@link CONSENT_CHANGED_EVENT} (same-tab).
 *
 * ``setKind`` writes localStorage AND posts to the server when the user is
 * signed in. On 401 it silently drops the server call — that's the
 * anonymous-user path. All other errors bubble so the caller (a button click
 * handler) can surface a toast if desired.
 */
export function useConsent(): {
  state: ConsentState;
  setKind: (kind: ConsentKind, granted: boolean) => Promise<void>;
  reload: () => void;
} {
  // Initialise to the empty state so SSR and the very first client render
  // agree. The real value lands in a layout effect.
  const [state, setState] = React.useState<ConsentState>(() => ({
    ...EMPTY_STATE,
  }));

  const reload = React.useCallback(() => {
    setState(getConsent());
  }, []);

  React.useEffect(() => {
    reload();

    const onStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY || event.key === STORAGE_VERSION_KEY) {
        reload();
      }
    };
    const onCustom = (event: Event) => {
      const detail = (event as CustomEvent<{ state?: ConsentState }>).detail;
      if (detail && detail.state) {
        setState(detail.state);
      } else {
        reload();
      }
    };

    window.addEventListener("storage", onStorage);
    window.addEventListener(CONSENT_CHANGED_EVENT, onCustom);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(CONSENT_CHANGED_EVENT, onCustom);
    };
  }, [reload]);

  const setKind = React.useCallback(
    async (kind: ConsentKind, granted: boolean) => {
      setConsent(kind, granted);
      try {
        await auth.setConsentRecord({ kind, granted });
      } catch (error) {
        // 401 = anonymous user; that's the expected branch when nobody is
        // signed in. Anything else (network / 5xx) we swallow so the
        // localStorage write — which IS the user's authoritative choice —
        // still lands. The banner UI never blocks on the server roundtrip.
        if (!(error instanceof ApiError) || error.status !== 401) {
          // Dedup the warn so a flapping network on a power-user toggling
          // every switch doesn't spam DevTools.
          logSyncFailure(kind, error);
        }
      }
    },
    [],
  );

  return { state, setKind, reload };
}

/**
 * One backoff retry per kind on the anonymous → signed-in sync. 2s is long
 * enough to ride out a transient 5xx / proxy blip, short enough that the
 * page-load animation has already settled and the user won't notice.
 */
const SYNC_RETRY_DELAY_MS = 2_000;

/** Dedup the "[consent.sync]" warn so a flaky network doesn't spam the
 *  console with one line per kind, per retry, per consent change. */
let warnedThisPageLoad = false;

function logSyncFailure(kind: ConsentKind, error: unknown): void {
  if (warnedThisPageLoad) return;
  warnedThisPageLoad = true;
  if (typeof console === "undefined") return;
  const message =
    error instanceof Error ? error.message : String(error ?? "unknown error");
  console.warn(`[consent.sync] failed (kind=${kind}): ${message}`);
}

/** Test-only escape hatch — resets the per-page-load warn flag and the
 *  ``syncedRef`` analog the caller controls. Internal use only. */
export function __resetConsentSyncWarnFlagForTests(): void {
  warnedThisPageLoad = false;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

async function postWithOneRetry(
  kind: ConsentKind,
  granted: boolean,
): Promise<void> {
  try {
    await auth.setConsentRecord({ kind, granted });
    return;
  } catch (firstError) {
    // 401 is "not signed in" — there's nothing to retry, the local record
    // is already the authoritative anonymous-path source of truth. Anything
    // else (5xx, network) gets one retry after a short backoff.
    if (firstError instanceof ApiError && firstError.status === 401) {
      throw firstError;
    }
    await delay(SYNC_RETRY_DELAY_MS);
    await auth.setConsentRecord({ kind, granted });
  }
}

/**
 * Reconciliation helper for the anonymous → signed-in transition. Walks each
 * non-null kind in ``state`` and posts it to the server so the audit trail
 * picks up the choice the user made before they had an account.
 *
 * Each kind gets one backoff retry on any non-401 failure (5xx, network);
 * after that we surface a single deduplicated ``[consent.sync]`` warn line
 * and **rethrow the underlying error** so the caller (``Header.tsx``) can
 * reset its ``syncedRef`` and re-attempt on a later mount / focus event.
 * 401 is the anonymous-path branch and is swallowed silently.
 */
export async function syncLocalConsentToServer(
  state: ConsentState,
): Promise<void> {
  const kinds: ConsentKind[] = ["analytics", "functional", "marketing"];
  const settled = await Promise.allSettled(
    kinds.map(async (kind) => {
      const record = state[kind];
      if (!record) return;
      try {
        await postWithOneRetry(kind, record.granted);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          // Anonymous path — local record stays authoritative, nothing to do.
          return;
        }
        logSyncFailure(kind, err);
        throw err;
      }
    }),
  );

  // If any kind terminally failed (both initial + retry), bubble the first
  // rejection so the caller can decide whether to re-arm the sync flag.
  for (const result of settled) {
    if (result.status === "rejected") {
      throw result.reason;
    }
  }
}
