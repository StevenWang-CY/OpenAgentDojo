"use client";

import * as React from "react";

/**
 * P1-5 — SSR-safe media query hook.
 *
 * Returns ``false`` on the server and on the very first client render so the
 * tree is hydration-stable; the effect then runs ``matchMedia`` and re-renders
 * with the real value on the next paint. Subscribing to ``change`` keeps the
 * value live across viewport resizes (devtools, window drag, orientation
 * flip).
 *
 * The fallback default (``initialValue``) lets a caller assert "render the
 * desktop layout on first paint" — useful for the three-way diff where we'd
 * rather render side-by-side optimistically than blink a tab control during
 * hydration. The effect still corrects mobile clients on their first commit.
 */
export function useMediaQuery(
  query: string,
  initialValue: boolean = false,
): boolean {
  const [matches, setMatches] = React.useState<boolean>(initialValue);

  React.useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const mql = window.matchMedia(query);

    // Read once on mount so a real-mobile client doesn't render desktop for
    // a full extra frame.
    setMatches(mql.matches);

    const handler = (event: MediaQueryListEvent): void => {
      setMatches(event.matches);
    };

    // Some older WebKit builds expose only ``addListener`` / ``removeListener``;
    // the type union lets us subscribe via whichever is present.
    if (typeof mql.addEventListener === "function") {
      mql.addEventListener("change", handler);
      return () => mql.removeEventListener("change", handler);
    }
    // Legacy fallback — Safari < 14.
    mql.addListener(handler);
    return () => mql.removeListener(handler);
  }, [query]);

  return matches;
}
