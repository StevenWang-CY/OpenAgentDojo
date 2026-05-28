"use client";

import * as React from "react";
import { GitMerge } from "lucide-react";
import { DiffViewer } from "@/components/workspace/DiffViewer";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { buildAnchorMap, EMPTY_ANCHOR_MAP } from "@/lib/diff-anchor-map";
import { useMediaQuery } from "@/lib/use-media-query";
import { useSynchronisedDiffScroll } from "@/lib/use-synchronised-diff-scroll";
import { env } from "@/lib/env";
import { track } from "@/lib/telemetry";
import { cn } from "@/lib/utils";
import {
  LoadBearingLineMarker,
  type LoadBearingMoment,
} from "./LoadBearingLineMarker";

/** P1-5 — public imperative handle. Parents (PostMortemWalkthrough) call
 *  ``scrollTo`` from the critical-moment scrubber so the diff panes track
 *  the timeline click. */
export interface ThreeWayDiffHandle {
  /** Scroll both diff panes to the line in the named file, when present.
   *  Silently no-ops when the file isn't in either diff (e.g. moments
   *  whose event payload didn't carry a file_path). */
  scrollTo: (file: string, line: number) => void;
}

interface ThreeWayDiffProps {
  /** The diff the user submitted (vs. initial commit). */
  userDiff: string;
  /** The canonical fix, vs. initial commit. */
  idealDiff: string;
  /** The agent's original patch (deliberately flawed). */
  agentPatchDiff: string | null;
  /** P1-5 — critical moments that resolve to a file + line range. Moments
   *  without a line anchor are passed through unmodified; the marker
   *  suppresses them gracefully. */
  criticalMoments?: LoadBearingMoment[];
  /** Telemetry hook proxied to {@link LoadBearingLineMarker}. */
  onLoadBearingLineHovered?: (
    info: { event_id: number; side: "user" | "ideal" },
  ) => void;
  className?: string;
}

const MOBILE_BREAKPOINT_PX = 960;

/**
 * P0-2 + P1-5 — three-way diff comparison.
 *
 * Two primary panes side by side (user vs. ideal) with synchronised scrolling
 * and a load-bearing-line overlay, plus an expandable third strip below
 * (agent's original patch). Below ~960px the layout collapses to a Radix
 * Tabs control so the report stays usable on mobile (FEATURE_GAPS P1-15).
 *
 * Falls back to a single-pane view when ideal/user are absent (e.g. tutorial
 * missions don't ship ideal_solution.diff) and to the legacy stacked layout
 * when the ``NEXT_PUBLIC_FEATURE_THREE_WAY_DIFF_V2`` flag is OFF (build-time
 * default ON; ``?diff_v2=0`` per-tab override forces legacy for the current
 * page render).
 */
export const ThreeWayDiff = React.forwardRef<
  ThreeWayDiffHandle,
  ThreeWayDiffProps
>(function ThreeWayDiff(
  {
    userDiff,
    idealDiff,
    agentPatchDiff,
    criticalMoments,
    onLoadBearingLineHovered,
    className,
  },
  forwardedRef,
) {
  const haveUser = userDiff.trim().length > 0;
  const haveIdeal = idealDiff.trim().length > 0;

  const v2Enabled = useV2Flag();

  const userPaneRef = React.useRef<HTMLDivElement | null>(null);
  const idealPaneRef = React.useRef<HTMLDivElement | null>(null);

  const anchorMap = React.useMemo(
    () =>
      haveUser && haveIdeal
        ? buildAnchorMap(userDiff, idealDiff)
        : EMPTY_ANCHOR_MAP,
    [haveUser, haveIdeal, userDiff, idealDiff],
  );

  // Imperative handle — exposed before the early returns to keep React's
  // hook order stable across "no content" renders. The ref objects
  // (``userPaneRef`` / ``idealPaneRef``) are stable across renders (a
  // React ref's identity never changes for the lifetime of the
  // component) so the empty deps array is intentional — the handle
  // closes over the ref OBJECTS, never their (mutable) ``.current``
  // values, so a stale closure isn't possible.
  React.useImperativeHandle(
    forwardedRef,
    (): ThreeWayDiffHandle => ({
      scrollTo(file, line) {
        scrollPaneToLine(userPaneRef.current, file, line);
        scrollPaneToLine(idealPaneRef.current, file, line);
      },
    }),
    [],
  );

  // Telemetry fan-out for the marker → caller, plus the local emit.
  const handleMarkerHover = React.useCallback(
    (moment: LoadBearingMoment, side: "user" | "ideal") => {
      track("load_bearing_line_hovered", {
        event_id: moment.event_id,
        side,
        moment_count: criticalMoments?.length ?? 0,
      });
      onLoadBearingLineHovered?.({ event_id: moment.event_id, side });
    },
    [criticalMoments, onLoadBearingLineHovered],
  );

  // The sync hook respects ``disabled`` so single-pane / legacy renders skip
  // wiring entirely.
  const syncDisabled = !v2Enabled || !haveUser || !haveIdeal;

  // FE remediation — the telemetry call is debounced per ``direction`` on
  // a 1-second window so a long drag-scroll emits one event instead of
  // ~60 (one per scroll tick). The actual scroll-sync UX is NOT debounced
  // — the partner pane still tracks the drag in real time. Only the
  // ``track()`` call is throttled here.
  const lastTrackedRef = React.useRef<Record<string, number>>({});
  const onPartnerScrolled = React.useCallback(
    ({
      direction,
      anchorCount,
    }: {
      direction: "user_to_ideal" | "ideal_to_user";
      anchorCount: number;
    }) => {
      const now = Date.now();
      const last = lastTrackedRef.current[direction] ?? 0;
      // 1s window — anything closer than 1000ms to the previous emit
      // for the same direction is suppressed so the funnel sees one
      // event per drag rather than a per-frame storm.
      if (now - last < 1000) {
        // Refresh the timestamp so a continuous drag keeps suppressing.
        lastTrackedRef.current[direction] = now;
        return;
      }
      lastTrackedRef.current[direction] = now;
      track("three_way_diff_synced_scroll", {
        direction,
        anchor_count: anchorCount,
      });
    },
    [],
  );

  useSynchronisedDiffScroll(userPaneRef, idealPaneRef, anchorMap, {
    disabled: syncDisabled,
    onPartnerScrolled,
  });

  if (!haveUser && !haveIdeal) {
    return null;
  }

  // Legacy fallback — preserved for the flag-OFF rollback path. The two-pane
  // layout is unchanged from the P0-2 ship; no scroll sync, no marker.
  if (!v2Enabled) {
    return (
      <LegacyStackedDiff
        userDiff={userDiff}
        idealDiff={idealDiff}
        agentPatchDiff={agentPatchDiff}
        haveUser={haveUser}
        haveIdeal={haveIdeal}
        className={className}
      />
    );
  }

  return (
    <ResponsiveThreeWayDiff
      userDiff={userDiff}
      idealDiff={idealDiff}
      agentPatchDiff={agentPatchDiff}
      haveUser={haveUser}
      haveIdeal={haveIdeal}
      criticalMoments={criticalMoments ?? []}
      userPaneRef={userPaneRef}
      idealPaneRef={idealPaneRef}
      onMarkerHover={handleMarkerHover}
      className={className}
    />
  );
});

/** Honour the build-time default, then layer the per-tab ``?diff_v2=`` override
 *  if present. Reads ``window.location`` lazily so SSR stays stable.
 *
 *  Parsing rules:
 *    * Truthy (override = true): ``"1"``, ``"true"``, ``"yes"``, or an empty
 *      string (``?diff_v2`` with no value behaves like a "force on" toggle).
 *    * Falsy (override = false): ``"0"``, ``"false"``, ``"no"``.
 *    * Anything else: fall through to the build-time default, so a typo
 *      doesn't silently force the user into the legacy renderer.
 */
function useV2Flag(): boolean {
  const [override, setOverride] = React.useState<boolean | null>(null);
  React.useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const params = new URLSearchParams(window.location.search);
      const raw = params.get("diff_v2");
      if (raw === null) {
        setOverride(null);
        return;
      }
      const normalised = raw.trim().toLowerCase();
      if (
        normalised === "1" ||
        normalised === "true" ||
        normalised === "yes" ||
        normalised === ""
      ) {
        setOverride(true);
        return;
      }
      if (
        normalised === "0" ||
        normalised === "false" ||
        normalised === "no"
      ) {
        setOverride(false);
        return;
      }
      // Unrecognised — fall through to the build-time default.
      setOverride(null);
    } catch {
      setOverride(null);
    }
  }, []);
  return override ?? env.threeWayDiffV2Default;
}

function scrollPaneToLine(
  pane: HTMLElement | null,
  file: string,
  line: number,
): boolean {
  if (!pane) return false;
  const candidates = [`diff-${file}-i${line}`, `diff-${file}-n${line}`, `diff-${file}-d${line}`];
  for (const id of candidates) {
    const el = pane.querySelector<HTMLElement>(`[id="${cssEscape(id)}"]`);
    if (!el) continue;
    const elRect = el.getBoundingClientRect();
    const paneRect = pane.getBoundingClientRect();
    pane.scrollTop = pane.scrollTop + (elRect.top - paneRect.top);
    return true;
  }
  return false;
}

function cssEscape(value: string): string {
  // Inside ``[id="..."]`` only ``"`` and ``\`` actually need escaping —
  // path-style ids with ``/`` and ``.`` are quote-safe. Using CSS.escape
  // would over-escape (it escapes ``/``), which jsdom's selector engine
  // then fails to match against the actual DOM id.
  return value.replace(/(["\\])/g, "\\$1");
}

interface ResponsiveProps {
  userDiff: string;
  idealDiff: string;
  agentPatchDiff: string | null;
  haveUser: boolean;
  haveIdeal: boolean;
  criticalMoments: LoadBearingMoment[];
  userPaneRef: React.RefObject<HTMLDivElement | null>;
  idealPaneRef: React.RefObject<HTMLDivElement | null>;
  onMarkerHover: (moment: LoadBearingMoment, side: "user" | "ideal") => void;
  className?: string;
}

function ResponsiveThreeWayDiff({
  userDiff,
  idealDiff,
  agentPatchDiff,
  haveUser,
  haveIdeal,
  criticalMoments,
  userPaneRef,
  idealPaneRef,
  onMarkerHover,
  className,
}: ResponsiveProps): React.JSX.Element {
  const isMobile = useMediaQuery(
    `(max-width: ${MOBILE_BREAKPOINT_PX - 1}px)`,
    false,
  );

  // The third strip is a *collapsible disclosure* on desktop and a *tab* on
  // mobile — same control, different surface to match the constraint.
  const [agentExpanded, setAgentExpanded] = React.useState(false);

  if (isMobile) {
    return (
      <Tabs
        defaultValue={haveUser ? "user" : "ideal"}
        data-testid="three-way-diff"
        data-layout="tabs"
        className={cn("grid gap-3", className)}
      >
        <TabsList className="self-start">
          <TabsTrigger value="user">Your submission</TabsTrigger>
          <TabsTrigger value="ideal">Ideal solution</TabsTrigger>
          {agentPatchDiff ? (
            <TabsTrigger value="agent">Agent&apos;s original patch</TabsTrigger>
          ) : null}
        </TabsList>
        <TabsContent value="user">
          <DiffPane
            label="you submitted"
            tone="user"
            diff={haveUser ? userDiff : ""}
            emptyLabel="No changes submitted."
            paneRef={userPaneRef}
            criticalMoments={criticalMoments}
            side="user"
            onMarkerHover={onMarkerHover}
          />
        </TabsContent>
        <TabsContent value="ideal">
          <DiffPane
            label="ideal solution"
            tone="ideal"
            diff={haveIdeal ? idealDiff : ""}
            emptyLabel="No canonical fix shipped for this mission."
            paneRef={idealPaneRef}
            criticalMoments={criticalMoments}
            side="ideal"
            onMarkerHover={onMarkerHover}
          />
        </TabsContent>
        {agentPatchDiff ? (
          <TabsContent value="agent">
            <AgentPatchStrip diff={agentPatchDiff} />
          </TabsContent>
        ) : null}
      </Tabs>
    );
  }

  return (
    <div
      className={cn("grid gap-4", className)}
      data-testid="three-way-diff"
      data-layout="side-by-side"
    >
      <div className="grid gap-4 lg:grid-cols-2">
        <DiffPane
          label="you submitted"
          tone="user"
          diff={haveUser ? userDiff : ""}
          emptyLabel="No changes submitted."
          paneRef={userPaneRef}
          criticalMoments={criticalMoments}
          side="user"
          onMarkerHover={onMarkerHover}
        />
        <DiffPane
          label="ideal solution"
          tone="ideal"
          diff={haveIdeal ? idealDiff : ""}
          emptyLabel="No canonical fix shipped for this mission."
          paneRef={idealPaneRef}
          criticalMoments={criticalMoments}
          side="ideal"
          onMarkerHover={onMarkerHover}
        />
      </div>
      {agentPatchDiff ? (
        <details
          className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]"
          onToggle={(e) =>
            setAgentExpanded((e.target as HTMLDetailsElement).open)
          }
        >
          <summary className="flex cursor-pointer items-center gap-2 px-4 py-2.5 font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
            <GitMerge className="size-3.5" aria-hidden />
            {"// agent's original patch"}
            <span className="ml-2 text-[10px] normal-case tracking-normal text-[var(--color-muted-foreground)]/70">
              {agentExpanded ? "(hide)" : "(show)"}
            </span>
          </summary>
          <div className="border-t border-[var(--color-border)] p-3">
            <AgentPatchStrip diff={agentPatchDiff} embedded />
          </div>
        </details>
      ) : null}
    </div>
  );
}

interface DiffPaneProps {
  label: string;
  tone: "user" | "ideal";
  diff: string;
  emptyLabel: string;
  paneRef: React.RefObject<HTMLDivElement | null>;
  criticalMoments: LoadBearingMoment[];
  side: "user" | "ideal";
  onMarkerHover: (moment: LoadBearingMoment, side: "user" | "ideal") => void;
}

function DiffPane({
  label,
  tone,
  diff,
  emptyLabel,
  paneRef,
  criticalMoments,
  side,
  onMarkerHover,
}: DiffPaneProps): React.JSX.Element {
  return (
    <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <header
        className={cn(
          "flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2 font-mono text-[10px] uppercase tracking-[0.18em]",
          tone === "ideal"
            ? "bg-[oklch(from_var(--color-success)_l_c_h/0.08)] text-[var(--color-success)]"
            : "bg-[var(--color-muted)] text-[var(--color-muted-foreground)]",
        )}
      >
        <span>{"// "}{label}</span>
      </header>
      <div
        className="relative max-h-[520px] overflow-auto p-3"
        ref={paneRef}
        data-testid={`three-way-diff-pane-${side}`}
      >
        {diff ? (
          <DiffViewer unifiedDiff={diff} defaultViewType="unified" />
        ) : (
          <p className="px-2 py-6 text-center text-sm text-[var(--color-muted-foreground)]">
            {emptyLabel}
          </p>
        )}
        {diff && criticalMoments.length > 0 ? (
          <LoadBearingLineMarker
            moments={criticalMoments}
            side={side}
            diffContainerRef={paneRef}
            onLoadBearingLineHovered={onMarkerHover}
          />
        ) : null}
      </div>
    </div>
  );
}

function AgentPatchStrip({
  diff,
  embedded = false,
}: {
  diff: string;
  embedded?: boolean;
}): React.JSX.Element {
  return (
    <div
      className={cn(
        !embedded
          ? "overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
          : "",
      )}
    >
      <DiffViewer unifiedDiff={diff} defaultViewType="unified" />
    </div>
  );
}

// ── Legacy stacked fallback ────────────────────────────────────────────────
// Preserved for the flag-OFF rollback. Mirrors the P0-2 ship exactly so the
// rollback path is byte-equivalent. Once the flag is removed in a follow-up
// release this whole block goes with it.

interface LegacyProps {
  userDiff: string;
  idealDiff: string;
  agentPatchDiff: string | null;
  haveUser: boolean;
  haveIdeal: boolean;
  className?: string;
}

function LegacyStackedDiff({
  userDiff,
  idealDiff,
  agentPatchDiff,
  haveUser,
  haveIdeal,
  className,
}: LegacyProps): React.JSX.Element {
  const [showAgent, setShowAgent] = React.useState(false);
  return (
    <div
      className={cn("grid gap-4", className)}
      data-testid="three-way-diff"
      data-layout="legacy"
    >
      <div className="grid gap-4 lg:grid-cols-2">
        <LegacyLayer
          label="you submitted"
          tone="user"
          diff={haveUser ? userDiff : ""}
          emptyLabel="No changes submitted."
        />
        <LegacyLayer
          label="ideal solution"
          tone="ideal"
          diff={haveIdeal ? idealDiff : ""}
          emptyLabel="No canonical fix shipped for this mission."
        />
      </div>
      {agentPatchDiff ? (
        <details
          className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]"
          onToggle={(e) => setShowAgent((e.target as HTMLDetailsElement).open)}
        >
          <summary className="flex cursor-pointer items-center gap-2 px-4 py-2.5 font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
            <GitMerge className="size-3.5" aria-hidden />
            {"// agent's original patch"}
            <span className="ml-2 text-[10px] normal-case tracking-normal text-[var(--color-muted-foreground)]/70">
              {showAgent ? "(hide)" : "(show)"}
            </span>
          </summary>
          <div className="border-t border-[var(--color-border)] p-3">
            <DiffViewer unifiedDiff={agentPatchDiff} defaultViewType="unified" />
          </div>
        </details>
      ) : null}
    </div>
  );
}

function LegacyLayer({
  label,
  tone,
  diff,
  emptyLabel,
}: {
  label: string;
  tone: "user" | "ideal";
  diff: string;
  emptyLabel: string;
}): React.JSX.Element {
  return (
    <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <header
        className={cn(
          "flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2 font-mono text-[10px] uppercase tracking-[0.18em]",
          tone === "ideal"
            ? "bg-[oklch(from_var(--color-success)_l_c_h/0.08)] text-[var(--color-success)]"
            : "bg-[var(--color-muted)] text-[var(--color-muted-foreground)]",
        )}
      >
        <span>{"// "}{label}</span>
      </header>
      <div className="p-3">
        {diff ? (
          <DiffViewer unifiedDiff={diff} defaultViewType="unified" />
        ) : (
          <p className="px-2 py-6 text-center text-sm text-[var(--color-muted-foreground)]">
            {emptyLabel}
          </p>
        )}
      </div>
    </div>
  );
}
