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
 *  verification" has no line anchor).
 *
 *  ``user_line_text`` + ``ideal_line_text`` are the optional raw line
 *  strings used by the token-level inline char-diff overlay. When both
 *  are present and the marker's tooltip opens, we render a token-diff
 *  strip inside the tooltip so the user can see precisely which tokens
 *  changed. Either may be omitted (e.g. the moment is anchored to a
 *  pure-insertion line that has no user counterpart); the overlay
 *  suppresses itself when either side is missing. */
export interface LoadBearingMoment {
  event_id: number;
  file_path?: string;
  start_line?: number;
  end_line?: number;
  label: string;
  user_line_text?: string;
  ideal_line_text?: string;
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

// ---------------------------------------------------------------------------
// Token-level char-diff (P1-5 "Open decisions": yes-for-token-level)
// ---------------------------------------------------------------------------

/** Soft cap on per-line token count fed into the LCS. Above this the diff
 *  collapses to a coarse single-pair (whole user line removed, whole ideal
 *  line added) so a pathological 1000-token line doesn't melt the renderer. */
const MAX_TOKENS_PER_LINE = 100;
/** Hard cap on line length before we bail out entirely. */
const MAX_LINE_CHARS = 400;

/**
 * Split ``line`` into a lossless token list. The split keeps every
 * delimiter (whitespace and Unicode punctuation) as its own token, so
 * concatenating the result is identical to the input. Empty inputs
 * produce an empty list.
 *
 * Examples:
 *   tokeniseLine("foo bar baz")        -> ["foo", " ", "bar", " ", "baz"]
 *   tokeniseLine("a,b c")              -> ["a", ",", "b", " ", "c"]
 *   tokeniseLine("hello, world!")      -> ["hello", ",", " ", "world", "!"]
 */
export function tokeniseLine(line: string): string[] {
  if (!line) return [];
  const tokens: string[] = [];
  // ``\p{P}`` matches any Unicode punctuation; ``\s`` covers whitespace.
  // The "u" flag is required for the Unicode property escape.
  const delim = /[\s\p{P}]/u;
  let buf = "";
  for (const ch of line) {
    if (delim.test(ch)) {
      if (buf.length > 0) {
        tokens.push(buf);
        buf = "";
      }
      tokens.push(ch);
    } else {
      buf += ch;
    }
  }
  if (buf.length > 0) tokens.push(buf);
  return tokens;
}

export type TokenDiffOp =
  | { kind: "equal"; value: string }
  | { kind: "removed"; value: string }
  | { kind: "added"; value: string };

/**
 * Token-level diff via classic LCS dynamic programming.
 *
 * The algorithm:
 *   1. Build the (M+1)×(N+1) LCS length table.
 *   2. Walk it backwards to recover a sequence of ``equal``/``removed``/
 *      ``added`` operations (we backtrack to the cell with the larger
 *      neighbour, treating ties as "prefer removed-first" so the output
 *      is deterministic).
 *   3. Reverse the recovered list so consumers iterate left-to-right.
 *
 * Both inputs are token arrays from ``tokeniseLine``. The result is a
 * flat list of ops the caller renders by mapping the ``kind`` to a CSS
 * class — see ``renderTokenDiff`` below.
 *
 * Complexity: O(M·N) time + space. For the 100-token cap that's at most
 * 10k cells per line, which is cheap relative to the rest of the diff
 * pane's render cost.
 */
export function tokenDiff(
  userTokens: string[],
  idealTokens: string[],
): TokenDiffOp[] {
  const m = userTokens.length;
  const n = idealTokens.length;
  if (m === 0 && n === 0) return [];
  if (m === 0) {
    return idealTokens.map((t) => ({ kind: "added" as const, value: t }));
  }
  if (n === 0) {
    return userTokens.map((t) => ({ kind: "removed" as const, value: t }));
  }
  // ``dp[i][j]`` = LCS length for userTokens[0..i) vs idealTokens[0..j).
  // We use a flat Int32 array for cache locality (and to avoid the
  // boxed-array overhead on hot paths).
  const stride = n + 1;
  const dp = new Int32Array((m + 1) * stride);
  for (let i = 1; i <= m; i += 1) {
    for (let j = 1; j <= n; j += 1) {
      if (userTokens[i - 1] === idealTokens[j - 1]) {
        dp[i * stride + j] = dp[(i - 1) * stride + (j - 1)]! + 1;
      } else {
        const up = dp[(i - 1) * stride + j]!;
        const left = dp[i * stride + (j - 1)]!;
        dp[i * stride + j] = up >= left ? up : left;
      }
    }
  }
  const out: TokenDiffOp[] = [];
  let i = m;
  let j = n;
  while (i > 0 && j > 0) {
    if (userTokens[i - 1] === idealTokens[j - 1]) {
      out.push({ kind: "equal", value: userTokens[i - 1]! });
      i -= 1;
      j -= 1;
      continue;
    }
    const up = dp[(i - 1) * stride + j]!;
    const left = dp[i * stride + (j - 1)]!;
    // Tie-break: prefer "removed" first so output ordering is
    // deterministic given equivalent LCS paths.
    if (up >= left) {
      out.push({ kind: "removed", value: userTokens[i - 1]! });
      i -= 1;
    } else {
      out.push({ kind: "added", value: idealTokens[j - 1]! });
      j -= 1;
    }
  }
  while (i > 0) {
    out.push({ kind: "removed", value: userTokens[i - 1]! });
    i -= 1;
  }
  while (j > 0) {
    out.push({ kind: "added", value: idealTokens[j - 1]! });
    j -= 1;
  }
  out.reverse();
  return out;
}

/**
 * Compute the token diff for the marker's tooltip, applying the two
 * safety caps. Returns ``null`` when the diff is suppressed (lines too
 * long, both sides missing, or identical).
 */
export function computeLoadBearingTokenDiff(
  userLine: string | undefined,
  idealLine: string | undefined,
): TokenDiffOp[] | null {
  if (typeof userLine !== "string" || typeof idealLine !== "string") {
    return null;
  }
  if (userLine === idealLine) return null;
  if (userLine.length > MAX_LINE_CHARS || idealLine.length > MAX_LINE_CHARS) {
    return null;
  }
  const userTokens = tokeniseLine(userLine);
  const idealTokens = tokeniseLine(idealLine);
  // Bail out into a coarse single-pair when either side is too long —
  // the LCS table would still fit but the user-facing diff stops being
  // useful past ~100 tokens.
  if (
    userTokens.length > MAX_TOKENS_PER_LINE ||
    idealTokens.length > MAX_TOKENS_PER_LINE
  ) {
    return [
      { kind: "removed", value: userLine },
      { kind: "added", value: idealLine },
    ];
  }
  return tokenDiff(userTokens, idealTokens);
}

/** Render a token diff as a small inline strip inside the tooltip.
 *  Added/removed tokens get the design-spec'd background tones; equal
 *  tokens pass through with no styling. */
function renderTokenDiff(ops: TokenDiffOp[]): React.JSX.Element {
  return (
    <span
      data-testid="load-bearing-token-diff"
      className="block whitespace-pre-wrap break-all font-mono text-[10px] leading-snug"
    >
      {ops.map((op, i) => {
        if (op.kind === "equal") {
          return (
            <span key={i} data-token-kind="equal">
              {op.value}
            </span>
          );
        }
        if (op.kind === "added") {
          return (
            <span
              key={i}
              data-token-kind="added"
              className="bg-green-500/15 text-green-300"
            >
              {op.value}
            </span>
          );
        }
        return (
          <span
            key={i}
            data-token-kind="removed"
            className="bg-red-500/15 text-red-300"
          >
            {op.value}
          </span>
        );
      })}
    </span>
  );
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
          // FE remediation — when the head moment carries both the user's
          // line and the ideal line, surface a token-level inline char
          // diff inside the tooltip. The helper caps long inputs so the
          // overlay can't blow up on pathological lines.
          const head = group[0];
          const tokenOps = computeLoadBearingTokenDiff(
            head?.user_line_text,
            head?.ideal_line_text,
          );
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
                {tokenOps ? (
                  <div className="mt-1.5 max-w-[260px] rounded border border-[var(--color-border)] bg-[var(--color-surface)] p-1.5">
                    {renderTokenDiff(tokenOps)}
                  </div>
                ) : null}
              </TooltipContent>
            </Tooltip>
          );
        })}
      </div>
    </TooltipProvider>
  );
}
