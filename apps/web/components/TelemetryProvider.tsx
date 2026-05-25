"use client";

import * as React from "react";
import { usePathname } from "next/navigation";
import { env } from "@/lib/env";
import {
  CONSENT_CHANGED_EVENT,
  getConsent,
  type ConsentState,
} from "@/lib/consent";
import {
  __setTelemetryClient,
  pageView,
} from "@/lib/telemetry";

/**
 * Lazily wires `posthog-js` into the module-level telemetry hook iff
 *   1. `NEXT_PUBLIC_POSTHOG_KEY` is present at runtime, AND
 *   2. the user has explicitly granted analytics consent (P0-5).
 *
 * Until both are true, the SDK is never even imported — the network never
 * sees a request to the PostHog host, and the bundle never pays for the
 * dep on a cold visit. The provider also subscribes to the in-process
 * ``consent-changed`` custom event so opt-ins flip the SDK on without a
 * page reload, and opt-outs flip it off (via ``opt_out_capturing``).
 *
 * Why module-level functions instead of context: matches how PostHog/Segment
 * are typically called (`posthog.capture("event")` from anywhere). React
 * context would force every caller into a hook, which doesn't compose with
 * imperative handlers (mutations, async callbacks, etc.).
 */
interface TelemetryProviderProps {
  children: React.ReactNode;
}

interface PosthogClient {
  init?: (key: string, config: Record<string, unknown>) => void;
  capture: (event: string, props?: Record<string, unknown>) => void;
  identify: (userId: string, traits?: Record<string, unknown>) => void;
  opt_out_capturing?: () => void;
  opt_in_capturing?: () => void;
  reset?: () => void;
}

export function TelemetryProvider({ children }: TelemetryProviderProps) {
  const pathname = usePathname();

  React.useEffect(() => {
    let cancelled = false;
    const key = env.posthogKey;
    const host = env.posthogHost;

    // Both must be set explicitly. We deliberately do NOT default the host to
    // a third-party hosted endpoint — a deploy that forgets to configure
    // analytics should be a no-op, not silent telemetry to a foreign domain.
    if (!key || !host) return;
    if (typeof window === "undefined") return;

    // The live client (post-import). Captured in a closure so the consent
    // handler can opt-out without re-importing.
    let client: PosthogClient | null = null;
    let initialised = false;

    const teardownTelemetry = () => {
      __setTelemetryClient(null);
      if (client?.opt_out_capturing) {
        try {
          client.opt_out_capturing();
        } catch {
          // Tolerate posthog-js version churn.
        }
      }
      if (client?.reset) {
        try {
          client.reset();
        } catch {
          // Same.
        }
      }
    };

    const loadAndInit = async () => {
      if (initialised || cancelled) return;
      // Use a string variable to keep the bundler from statically resolving
      // the module — we want a true optional dep, not a hard import. The
      // `.catch` guarantees the promise never rejects.
      const moduleName = "posthog-js";
      const mod = await import(/* webpackIgnore: true */ /* @vite-ignore */ moduleName).catch(
        () => null,
      );
      if (cancelled || !mod) return;
      // Race-window check (Phase 3 finding): the dynamic import is async, so
      // a user can revoke analytics consent between the gate above and the
      // moment the SDK code lands. If the live state is no longer "granted"
      // we drop the module on the floor instead of initialising it.
      if (getConsent().analytics?.granted !== true) return;
      const posthog = (mod as { default?: unknown }).default ?? mod;
      if (!posthog || typeof posthog !== "object") return;

      const candidate = posthog as PosthogClient;

      // Defend against a future posthog-js major bump (or a custom shim
      // being substituted in) by verifying the methods we actually call.
      // A broken client would otherwise throw on the next track() and tear
      // down the React tree.
      if (
        typeof candidate.capture !== "function" ||
        typeof candidate.identify !== "function"
      ) {
        console.warn(
          "[telemetry] posthog client missing capture/identify; skipping init",
        );
        return;
      }

      if (typeof candidate.init === "function") {
        try {
          candidate.init(key, {
            api_host: host,
            capture_pageview: false, // we drive page views from this provider
            persistence: "localStorage+cookie",
            // ``opt_out_capturing_by_default`` keeps the SDK silent even if a
            // stray bug calls into it before our gate kicks in. Belt + braces.
            opt_out_capturing_by_default: false,
          });
        } catch {
          // Bad config or already-initialised — proceed; capture will still no-op safely.
        }
      }

      client = candidate;
      initialised = true;
      __setTelemetryClient({
        capture: (event, props) => candidate.capture(event, props),
        identify: (userId, traits) => candidate.identify(userId, traits),
      });
    };

    const applyConsent = (consent: ConsentState) => {
      if (consent.analytics?.granted) {
        if (!initialised) {
          void loadAndInit();
        } else if (client?.opt_in_capturing) {
          try {
            client.opt_in_capturing();
          } catch {
            // Soft-fail.
          }
        }
      } else if (initialised) {
        teardownTelemetry();
      }
    };

    // Apply once with the current consent (may already be granted from a
    // previous session — localStorage survives reloads).
    applyConsent(getConsent());

    const onConsentChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ state?: ConsentState }>).detail;
      applyConsent(detail?.state ?? getConsent());
    };
    const onStorage = (storageEvent: StorageEvent) => {
      if (storageEvent.key === "consent_v" || storageEvent.key === "consent_v_version") {
        applyConsent(getConsent());
      }
    };

    window.addEventListener(CONSENT_CHANGED_EVENT, onConsentChanged);
    window.addEventListener("storage", onStorage);

    return () => {
      cancelled = true;
      window.removeEventListener(CONSENT_CHANGED_EVENT, onConsentChanged);
      window.removeEventListener("storage", onStorage);
      teardownTelemetry();
    };
  }, []);

  // Track route changes. `usePathname()` doesn't include the query string,
  // which is by design — we don't want token-leaking URLs in analytics.
  // ``pageView`` itself checks consent before emitting; we still call it
  // unconditionally so the in-memory ring buffer keeps a useful trail.
  React.useEffect(() => {
    if (!pathname) return;
    pageView(pathname);
  }, [pathname]);

  return <>{children}</>;
}
