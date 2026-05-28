/**
 * P1-5 — anchor-map utility for the synchronised three-way diff.
 *
 * Given two unified diffs ``A`` (user) and ``B`` (ideal), produce a sorted
 * array of ``(aLine, bLine, file)`` anchor pairs at hunk boundaries. The
 * ``useSynchronisedDiffScroll`` hook reads this map and interpolates lines
 * between anchors so a scroll in pane A maps proportionally to pane B.
 *
 * Pairing strategy: for each common file (matched by ``oldPath`` / ``newPath``),
 * pair hunks in order — hunk N of A pairs with hunk N of B; the anchor point
 * is the first changed line of each hunk (insert/delete; ``newStart`` is the
 * fallback). When one side has more hunks than the other, the extra hunks
 * produce no anchors — the partner pane just doesn't sync past the last
 * pairable hunk for that file, which is the right behaviour.
 *
 * Files appearing in only one side produce no anchors at all (defensive — the
 * diff still renders, scroll just doesn't sync across that file). The
 * downstream marker layer treats those files as un-anchored and suppresses the
 * load-bearing chip for them.
 */
import { parseDiff, type ChangeData, type HunkData } from "react-diff-view";
import gitDiffParser from "gitdiff-parser";

export interface DiffAnchor {
  /** 1-indexed line in pane A (user) where the anchor sits. */
  aLine: number;
  /** 1-indexed line in pane B (ideal) where the anchor sits. */
  bLine: number;
  /** ``newPath`` of the file the anchor belongs to. ``oldPath`` is used as
   *  a fallback for pure-delete hunks. */
  file: string;
}

/**
 * P1-5 — built anchor map. ``anchors`` is sorted ascending by ``aLine``
 * (for the A → B direction); ``bSortedIndices`` is a parallel array of
 * indices into ``anchors`` sorted ascending by ``bLine`` (for the B → A
 * direction). Both views are computed once by ``buildAnchorMap`` so the
 * synchronised-scroll hook never allocates a sorted copy on the hot path.
 */
export interface DiffAnchorMap {
  anchors: DiffAnchor[];
  bSortedIndices: number[];
}

interface ParsedFile {
  oldPath?: string;
  newPath?: string;
  hunks?: HunkData[];
}

/**
 * Best-effort parser — react-diff-view's ``parseDiff`` is strict about Git's
 * extended headers; ``gitdiff-parser`` is more forgiving (handles output from
 * ``git apply --3way`` and a few of its less-canonical cousins). We try the
 * strict parser first because its shape is already what the renderer
 * consumes, and fall through to the lenient parser on miss / throw — exactly
 * the same pattern ``DiffViewer`` uses for its own files list.
 */
function parseToFiles(diff: string): ParsedFile[] {
  const trimmed = diff.trim();
  if (trimmed.length === 0) return [];
  try {
    const primary = parseDiff(diff) as ParsedFile[];
    if (primary.length > 0) return primary;
  } catch {
    /* fall through to gitdiff-parser */
  }
  try {
    // gitdiff-parser .d.ts lags react-diff-view's expectations; the runtime
    // shape (.hunks etc.) is structurally compatible — cast is safe and
    // intentional.
    return gitDiffParser.parse(diff) as unknown as ParsedFile[];
  } catch {
    return [];
  }
}

/** Canonical key for matching a file across the two diffs. We prefer
 *  ``newPath`` (post-image) because that's the path the user sees in the
 *  workspace; ``oldPath`` is the fallback for pure-delete files. */
function fileKey(f: ParsedFile): string {
  return f.newPath ?? f.oldPath ?? "";
}

/** First changed (insert/delete) line in a hunk; the line carries meaningful
 *  diff content rather than context. Falls back to the hunk's start line. */
function firstChangedLine(
  hunk: HunkData,
  axis: "old" | "new",
): number {
  const changes = (hunk.changes ?? []) as ChangeData[];
  for (const c of changes) {
    if (axis === "new") {
      if (c.type === "insert") return c.lineNumber;
      if (c.type === "normal") return c.newLineNumber;
    } else {
      if (c.type === "delete") return c.lineNumber;
      if (c.type === "normal") return c.oldLineNumber;
    }
  }
  // Fallback — every hunk has the canonical hunk header start.
  return axis === "new" ? hunk.newStart : hunk.oldStart;
}

/**
 * Build the anchor map. ``anchors`` is sorted by ``aLine`` ascending so the
 * scroll-sync hook can binary-search by the user-pane line; ``bSortedIndices``
 * is a parallel view sorted by ``bLine`` so the inverse (ideal → user)
 * lookup is also O(log n) without allocating a sorted copy per scroll event.
 *
 * Determinism: same inputs always produce the same arrays — file iteration
 * preserves the input order from ``parseDiff``, hunks within a file are
 * paired by index. No tie-break is needed beyond that.
 */
export function buildAnchorMap(
  userDiff: string,
  idealDiff: string,
): DiffAnchorMap {
  const aFiles = parseToFiles(userDiff);
  const bFiles = parseToFiles(idealDiff);

  // Build a lookup from file key → ideal-side file so we can pair without
  // depending on the (potentially different) iteration orders.
  const bByKey = new Map<string, ParsedFile>();
  for (const f of bFiles) {
    const key = fileKey(f);
    if (key.length === 0) continue;
    // First occurrence wins — duplicate paths in a single diff are
    // pathological but we don't want a later entry to silently shadow
    // the earlier one's anchors.
    if (!bByKey.has(key)) bByKey.set(key, f);
  }

  const anchors: DiffAnchor[] = [];
  for (const aFile of aFiles) {
    const key = fileKey(aFile);
    if (key.length === 0) continue;
    const bFile = bByKey.get(key);
    if (!bFile) continue; // file only in A → no anchors
    const aHunks = (aFile.hunks ?? []) as HunkData[];
    const bHunks = (bFile.hunks ?? []) as HunkData[];
    const pairCount = Math.min(aHunks.length, bHunks.length);
    for (let i = 0; i < pairCount; i += 1) {
      const aHunk = aHunks[i];
      const bHunk = bHunks[i];
      if (!aHunk || !bHunk) continue;
      anchors.push({
        aLine: firstChangedLine(aHunk, "new"),
        bLine: firstChangedLine(bHunk, "new"),
        file: key,
      });
    }
  }

  // Sort by aLine asc — stable; ties (same aLine across two files) preserve
  // input order so the binary search picks the file the user is reading.
  anchors.sort((x, y) => x.aLine - y.aLine);

  // Pre-compute the B-sorted index view once. Holding indices rather than a
  // copy of the anchors lets the inverse search short-circuit back into the
  // original ``anchors`` array without a second allocation per lookup.
  const bSortedIndices = anchors.map((_, i) => i);
  bSortedIndices.sort((x, y) => {
    const ax = anchors[x];
    const ay = anchors[y];
    if (!ax || !ay) return 0;
    return ax.bLine - ay.bLine;
  });

  return { anchors, bSortedIndices };
}

/**
 * Locate the index in ``map.anchors`` whose ``aLine`` is the largest value
 * <= ``target``. Returns -1 when ``target`` precedes the first anchor.
 *
 * Pure binary search over the A-sorted view — keeps the scroll-sync hook
 * O(log n) per scroll event.
 */
export function findAnchorIndexByA(
  map: DiffAnchorMap,
  target: number,
): number {
  const { anchors } = map;
  let lo = 0;
  let hi = anchors.length - 1;
  let best = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const cur = anchors[mid];
    if (!cur) break;
    if (cur.aLine <= target) {
      best = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return best;
}

/**
 * Symmetric variant for the B → A direction. Returns the index INTO
 * ``map.anchors`` whose ``bLine`` is the largest value <= ``target``.
 *
 * Uses the pre-sorted ``map.bSortedIndices`` view so the lookup is O(log n)
 * with zero allocation per call — the previous implementation rebuilt the
 * sorted view on every scroll event.
 */
export function findAnchorIndexByB(
  map: DiffAnchorMap,
  target: number,
): number {
  const { anchors, bSortedIndices } = map;
  let lo = 0;
  let hi = bSortedIndices.length - 1;
  let bestI = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const idx = bSortedIndices[mid];
    if (idx === undefined) break;
    const cur = anchors[idx];
    if (!cur) break;
    if (cur.bLine <= target) {
      bestI = idx;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return bestI;
}

/** Empty / sentinel map — handy for the disabled-pane path. */
export const EMPTY_ANCHOR_MAP: DiffAnchorMap = {
  anchors: [],
  bSortedIndices: [],
};

/**
 * Given the anchors index returned by ``findAnchorIndexByB``, walk the
 * B-sorted view forward one step and return the next anchor by ``bLine``
 * (or ``null`` if ``prevAnchorIdx`` is already the last anchor on the B
 * axis). Lets the scroll-sync hook interpolate against the *B-side* next
 * anchor rather than the next entry in the A-sorted view, which would
 * pick the wrong neighbour when bLine ordering disagrees with aLine
 * ordering.
 */
export function nextAnchorByB(
  map: DiffAnchorMap,
  prevAnchorIdx: number,
): DiffAnchor | null {
  if (prevAnchorIdx < 0) {
    // Before the first B anchor — the "next" is the smallest bLine.
    const firstIdx = map.bSortedIndices[0];
    return firstIdx === undefined ? null : (map.anchors[firstIdx] ?? null);
  }
  // Find prevAnchorIdx's position in the B-sorted view, then step forward.
  const pos = map.bSortedIndices.indexOf(prevAnchorIdx);
  if (pos === -1) return null;
  const nextIdx = map.bSortedIndices[pos + 1];
  if (nextIdx === undefined) return null;
  return map.anchors[nextIdx] ?? null;
}

/**
 * Given the two surrounding anchors and the target line on side A, linearly
 * interpolate the corresponding line on side B. Used for lines that fall
 * between hunk boundaries.
 */
export function interpolateLine(
  prev: DiffAnchor | null,
  next: DiffAnchor | null,
  axis: "aToB" | "bToA",
  target: number,
): number {
  if (!prev && !next) return target;
  if (!prev) {
    // Before the first anchor — clamp to the partner's first anchor.
    return axis === "aToB" ? (next?.bLine ?? target) : (next?.aLine ?? target);
  }
  if (!next) {
    // After the last anchor — extrapolate by adding the source delta.
    const delta = target - (axis === "aToB" ? prev.aLine : prev.bLine);
    return (axis === "aToB" ? prev.bLine : prev.aLine) + delta;
  }
  if (axis === "aToB") {
    const span = next.aLine - prev.aLine;
    if (span <= 0) return prev.bLine;
    const ratio = (target - prev.aLine) / span;
    return prev.bLine + ratio * (next.bLine - prev.bLine);
  }
  const span = next.bLine - prev.bLine;
  if (span <= 0) return prev.aLine;
  const ratio = (target - prev.bLine) / span;
  return prev.aLine + ratio * (next.aLine - prev.aLine);
}
