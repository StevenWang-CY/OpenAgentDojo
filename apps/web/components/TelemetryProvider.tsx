"use client";

import * as React from "react";
import { usePathname } from "next/navigation";
import {
  __setTelemetryClient,
  pageView,
} from "@/lib/telemetry";

/**
 * Lazily wires `posthog-js` into the module-level telemetry hook iff
 * `NEXT_PUBLIC_POSTHOG_KEY` is present at runtime. When absent (local dev,
 * preview, or any privacy-respecting deployment) this component is a no-op
 * other than the page-view ring buffer.
 *
 * Why module-level functions instead of context: matches how PostHog/Segment
 * are typically called (`posthog.capture("event")` from anywhere). React
 * context would force every caller into a hook, which doesn't compose with
 * imperative handlers (mutations, async callbacks, etc.).
 */
interface TelemetryProviderProps {
  children: React.ReactNode;
}

export function TelemetryProvider({ children }: TelemetryProviderProps) {
  const pathname = usePathname();

  // One-shot dynamic import of posthog-js. If the dep is absent or the key
  // is unset, we bail silently — `track`/`identify`/`pageView` keep working
  // against the in-memory ring buffer.
  React.useEffect(() => {
    let cancelled = false;
    const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
    const host =
      process.env.NEXT_PUBLIC_POSTHOG_HOST ?? "https://us.i.posthog.com";

    if (!key) return;
    if (typeof window === "undefined") return;

    // Use a string variable to keep the bundler from statically resolving the
    // module — we want a true optional dep, not a hard import. The `.catch`
    // guarantees the promise never rejects.
    void (async () => {
      const moduleName = "posthog-js";
      const mod = await import(/* webpackIgnore: true */ /* @vite-ignore */ moduleName).catch(
        () => null
      );
      if (cancelled || !mod) return;
      const posthog = (mod as { default?: unknown }).default ?? mod;
      if (!posthog || typeof posthog !== "object") return;

      const client = posthog as {
        init?: (key: string, config: Record<string, unknown>) => void;
        capture: (event: string, props?: Record<string, unknown>) => void;
        identify: (userId: string, traits?: Record<string, unknown>) => void;
      };

      if (typeof client.init === "function") {
        try {
          client.init(key, {
            api_host: host,
            capture_pageview: false, // we drive page views from this provider
            persistence: "localStorage+cookie",
          });
        } catch {
          // Bad config or already-initialised — proceed; capture will still no-op safely.
        }
      }

      __setTelemetryClient({
        capture: (event, props) => client.capture(event, props),
        identify: (userId, traits) => client.identify(userId, traits),
      });
    })();

    return () => {
      cancelled = true;
      __setTelemetryClient(null);
    };
  }, []);

  // Track route changes. `usePathname()` doesn't include the query string,
  // which is by design — we don't want token-leaking URLs in analytics.
  React.useEffect(() => {
    if (!pathname) return;
    pageView(pathname);
  }, [pathname]);

  return <>{children}</>;
}
