"use client";

import * as React from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/Tooltip";
import { cn } from "@/lib/utils";

/** P1-5 — one critical moment as the marker sees it. ``file_path`` /
 *  ``start_line`` are the optional anchor; when absent the marker is
 *  suppressed for that moment (per design — "submitted without
 *  verification" has no line anchor). */
export interface LoadBearingMoment {
  event_id: number;
  file_path?: string;
  start_line?: number;
  end_line?: number;
  label: string;
}

interface LoadBearingLineMarkerProps {
  /** All moments for the active diff. The component groups by
   *  ``(file_path, start_line)`` and renders one chip per unique pair. */
  moments: LoadBearingMoment[];
  /** Which pane this marker overlays — drives the tooltip wording. */
  side: "user" | "ideal";
  /** The scrollable diff container; marker positions are computed
   *  relative to this element. */
  diffContainerRef: React.RefObject<HTMLElement | null>;
  /** Telemetry hook fired on first hover/focus of a given moment. */
  onLoadBearingLineHovered?: (
    moment: LoadBearingMoment,
    side: "user" | "ideal",
  ) => void;
}

interface PositionedMarker {
  /** Composite key — first event id in the group. */
  key: string;
  /** Top offset (px) of the chip inside the diff container. */
  top: number;
  /** All moments that landed on this line. Deduped by event_id. */
  group: LoadBearingMoment[];
}

/**
 * Group moments by ``(file, line)`` so the chip aggregates rather than
 * stacking. Moments without a line anchor are dropped — the suppress-rule.
 */
function groupMomentsByLine(
  moments: LoadBearingMoment[],
): Map<string, LoadBearingMoment[]> {
  const groups = new Map<string, LoadBearingMoment[]>();
  for (const m of moments) {
    if (!m.file_path || typeof m.start_line !== "number") continue;
    const key = `${m.file_path}::${m.start_line}`;
    const list = groups.get(key);
    if (list) {
      // Dedupe by event id so a re-emitted moment doesn't double-count.
      if (!list.some((e) => e.event_id === m.event_id)) list.push(m);
    } else {
      groups.set(key, [m]);
    }
  }
  return groups;
}

/**
 * The diff renderer (react-diff-view) stamps each change with a stable id
 * via ``generateAnchorID`` — see DiffViewer.tsx, which produces
 * ``diff-{file}-{n|i|d}{line}``. We probe all three flavours (insert /
 * delete / normal) in that priority order because a "load-bearing" line
 * could be any change type.
 */
function findAnchorElement(
  container: HTMLElement | null,
  file: string,
  line: number,
): HTMLElement | null {
  if (!container) return null;
  // The anchor ids are document-globally unique — querySelector inside the
  // container is fine and avoids matching the same id on a sibling pane.
  const candidates = [`diff-${file}-i${line}`, `diff-${file}-n${line}`, `diff-${file}-d${line}`];
  for (const id of candidates) {
    // ``getElementById`` is faster but the id may collide across panes;
    // scoped CSS attribute selector keeps the lookup pane-local.
    const el = container.querySelector<HTMLElement>(
      `[id="${cssEscape(id)}"]`,
    );
    if (el) return el;
  }
  return null;
}

/** Inside ``[id="..."]`` only ``"`` and ``\`` actually need escaping; using
 *  ``CSS.escape`` would over-escape ``/`` which jsdom's selector engine then
 *  fails to match against the actual DOM id. Path-style ids are quote-safe. */
function cssEscape(value: string): string {
  return value.replace(/(["\\])/g, "\\$1");
}

const TOOLTIP_USER = "this line is the one the agent got wrong";
const TOOLTIP_IDEAL = "this line is the fix you missed";

/**
 * Subtle left-gutter chip that pins to load-bearing lines in a diff pane.
 *
 * Hover (or keyboard focus) reveals the per-moment explanation; when
 * multiple moments collapse on the same line the tooltip aggregates them
 * ("3 moments at this line: …"). Position is recomputed on scroll +
 * resize via ResizeObserver — keeps the chip glued to its line as the
 * synchronised-scroll hook drives the pane.
 */
export function LoadBearingLineMarker({
  moments,
  side,
  diffContainerRef,
  onLoadBearingLineHovered,
}: LoadBearingLineMarkerProps): React.JSX.Element | null {
  const groups = React.useMemo(() => groupMomentsByLine(moments), [moments]);
  const [positions, setPositions] = React.useState<PositionedMarker[]>([]);
  const firedRef = React.useRef<Set<string>>(new Set());

  const recompute = React.useCallback(() => {
    const container = diffContainerRef.current;
    if (!container) {
      setPositions([]);
      return;
    }
    const containerRect = container.getBoundingClientRect();
    const out: PositionedMarker[] = [];
    for (const [key, group] of groups) {
      const head = group[0];
      if (!head?.file_path || typeof head.start_line !== "number") continue;
      const anchor = findAnchorElement(container, head.file_path, head.start_line);
      if (!anchor) continue;
      const anchorRect = anchor.getBoundingClientRect();
      // Relative top inside the (scrolled) container.
      const top = anchorRect.top - containerRect.top + container.scrollTop;
      out.push({ key, top, group });
    }
    setPositions(out);
  }, [diffContainerRef, groups]);

  React.useEffect(() => {
    recompute();
    const container = diffContainerRef.current;
    if (!container) return;

    let ro: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(() => recompute());
      ro.observe(container);
    }
    const onScroll = (): void => recompute();
    container.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);

    return () => {
      container.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (ro) ro.disconnect();
    };
  }, [diffContainerRef, recompute]);

  if (positions.length === 0) return null;

  function handleOpen(group: LoadBearingMoment[]): void {
    for (const m of group) {
      const fingerprint = `${m.event_id}::${side}`;
      if (firedRef.current.has(fingerprint)) continue;
      firedRef.current.add(fingerprint);
      onLoadBearingLineHovered?.(m, side);
    }
  }

  return (
    <TooltipProvider delayDuration={120}>
      <div
        aria-hidden={false}
        data-testid={`load-bearing-overlay-${side}`}
        className="pointer-events-none absolute inset-0"
      >
        {positions.map(({ key, top, group }) => {
          const tooltipBase = side === "user" ? TOOLTIP_USER : TOOLTIP_IDEAL;
          const tooltipText =
            group.length === 1
              ? `${tooltipBase} — ${group[0]?.label ?? ""}`.trim()
              : `${group.length} moments at this line: ${group
                  .map((m) => m.label)
                  .join("; ")}`;
          return (
            <Tooltip
              key={key}
              onOpenChange={(open) => {
                if (open) handleOpen(group);
              }}
            >
              <TooltipTrigger asChild>
                <button
                  type="button"
                  data-testid={`load-bearing-marker-${side}`}
                  data-event-id={group[0]?.event_id ?? ""}
                  data-group-size={group.length}
                  className={cn(
                    "pointer-events-auto absolute left-0 z-20 grid h-4 w-2.5 place-items-center",
                    "rounded-sm border-[var(--color-border)] bg-[var(--color-surface-elevated)]",
                    "shadow-soft transition-transform duration-150 ease-out hover:scale-110",
                    "motion-reduce:transition-none motion-reduce:hover:scale-100",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--color-surface)]",
                  )}
                  style={{ top: `${top}px` }}
                  aria-label={tooltipText}
                >
                  <span
                    aria-hidden
                    className={cn(
                      "block size-0",
                      "border-y-[5px] border-l-[7px] border-y-transparent",
                      side === "user"
                        ? "border-l-[var(--color-danger)]"
                        : "border-l-[var(--color-success)]",
                    )}
                  />
                </button>
              </TooltipTrigger>
              <TooltipContent side="right" align="center">
                <p className="max-w-[260px] text-xs leading-snug">{tooltipText}</p>
              </TooltipContent>
            </Tooltip>
          );
        })}
      </div>
    </TooltipProvider>
  );
}
