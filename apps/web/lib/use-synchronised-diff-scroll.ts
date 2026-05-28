"use client";

import * as React from "react";
import {
  findAnchorIndexByA,
  findAnchorIndexByB,
  interpolateLine,
  nextAnchorByB,
  type DiffAnchorMap,
} from "./diff-anchor-map";

/**
 * P1-5 — keep two diff panes in sync by their hunk-anchor map.
 *
 * Behaviour:
 *   - Listens to ``scroll`` on both panes.
 *   - Estimates the line currently at the top of the source pane from its
 *     ``scrollTop`` and ``scrollHeight`` (lines per pane = scrollHeight /
 *     line-height). Looks up the surrounding anchor pair, interpolates the
 *     partner line, and scrolls the partner.
 *   - Cycle-protected with a tick guard: when the hook drives the partner's
 *     ``scrollTop``, a "we just wrote" flag suppresses the partner's own
 *     scroll handler for the next animation frame so A→B→A doesn't loop.
 *   - Damped: the partner's scroll is set with the proportional anchor
 *     target, not a continuous re-interpolation per pixel — avoids the
 *     bounce that a naive percentage-sync produces.
 *
 * The hook is intentionally generic — it does not know about
 * react-diff-view. The DOM contract is "the element passed in is the
 * scrollable container that wraps the rendered diff." The anchor map's
 * coordinate system is line numbers (1-indexed).
 *
 * ``onPartnerScrolled`` (optional) fires once per logical scroll-then-sync
 * cycle; the consumer (ThreeWayDiff) uses it to emit the
 * ``three_way_diff_synced_scroll`` telemetry event.
 */
export interface SynchronisedScrollOptions {
  /** Estimated px per source line. Defaults to 18 (react-diff-view at 12px). */
  pixelsPerLine?: number;
  /** Telemetry hook fired when the partner pane was actually scrolled. */
  onPartnerScrolled?: (info: {
    direction: "user_to_ideal" | "ideal_to_user";
    anchorCount: number;
  }) => void;
  /** Disable the sync entirely (kept here so the parent can flip it off
   *  when only one pane is present). */
  disabled?: boolean;
}

export function useSynchronisedDiffScroll(
  refA: React.RefObject<HTMLElement | null>,
  refB: React.RefObject<HTMLElement | null>,
  anchorMap: DiffAnchorMap,
  options: SynchronisedScrollOptions = {},
): void {
  const pixelsPerLine = options.pixelsPerLine ?? 18;
  const onPartnerScrolled = options.onPartnerScrolled;
  const disabled = options.disabled ?? false;

  // Guard ref: when true, the next scroll event on the *other* pane is the
  // synthetic one we just wrote and should be ignored.
  const lockRef = React.useRef<"none" | "a-locked" | "b-locked">("none");
  // rAF token so we don't queue an unbounded backlog of scrolls under a
  // long drag — only the latest target lands.
  const rafRef = React.useRef<number | null>(null);

  React.useEffect(() => {
    if (disabled) return;
    const elA = refA.current;
    const elB = refB.current;
    if (!elA || !elB) return;
    const { anchors } = anchorMap;
    if (anchors.length === 0) return;

    function scrollTargetForA(): number | null {
      if (!elA || !elB) return null;
      const topLineA = elA.scrollTop / pixelsPerLine;
      const idx = findAnchorIndexByA(anchorMap, topLineA);
      const prev = idx >= 0 ? anchors[idx] : null;
      const next =
        idx + 1 < anchors.length ? anchors[idx + 1] : null;
      const targetLineB = interpolateLine(
        prev ?? null,
        next ?? null,
        "aToB",
        topLineA,
      );
      return Math.max(0, Math.round(targetLineB * pixelsPerLine));
    }

    function scrollTargetForB(): number | null {
      if (!elA || !elB) return null;
      const topLineB = elB.scrollTop / pixelsPerLine;
      const idx = findAnchorIndexByB(anchorMap, topLineB);
      const prev = idx >= 0 ? anchors[idx] : null;
      // ``findAnchorIndexByB`` returns an index into ``anchors`` (A-sorted),
      // but the B-side "next" anchor is the next greatest bLine, which is
      // the next entry in the pre-sorted B view. We surface that via the
      // sibling helper rather than re-sorting here.
      const next = nextAnchorByB(anchorMap, idx);
      const targetLineA = interpolateLine(
        prev ?? null,
        next ?? null,
        "bToA",
        topLineB,
      );
      return Math.max(0, Math.round(targetLineA * pixelsPerLine));
    }

    function schedule(
      direction: "user_to_ideal" | "ideal_to_user",
      computeTarget: () => number | null,
    ): void {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
      }
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        const target = computeTarget();
        if (target === null) return;
        const partner = direction === "user_to_ideal" ? elB : elA;
        if (!partner) return;
        // Same target → nothing to do (damping).
        if (Math.abs(partner.scrollTop - target) < 1) return;
        // Lock the partner so its own scroll handler short-circuits the
        // synthetic event we're about to fire.
        lockRef.current = direction === "user_to_ideal" ? "b-locked" : "a-locked";
        partner.scrollTop = target;
        if (onPartnerScrolled) {
          onPartnerScrolled({ direction, anchorCount: anchors.length });
        }
      });
    }

    function onScrollA(): void {
      if (lockRef.current === "a-locked") {
        lockRef.current = "none";
        return;
      }
      schedule("user_to_ideal", scrollTargetForA);
    }

    function onScrollB(): void {
      if (lockRef.current === "b-locked") {
        lockRef.current = "none";
        return;
      }
      schedule("ideal_to_user", scrollTargetForB);
    }

    elA.addEventListener("scroll", onScrollA, { passive: true });
    elB.addEventListener("scroll", onScrollB, { passive: true });

    return () => {
      elA.removeEventListener("scroll", onScrollA);
      elB.removeEventListener("scroll", onScrollB);
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      lockRef.current = "none";
    };
    // `anchorMap` is the only structural dep — pixelsPerLine + callback
    // identity changes shouldn't re-bind the listeners.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorMap, disabled, refA, refB]);
}
