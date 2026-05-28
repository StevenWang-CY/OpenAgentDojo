/**
 * P1-5 — ThreeWayDiff + supporting utilities tests.
 *
 * Covers:
 *   - buildAnchorMap pairs hunks of shared files; suppresses cross-file pairs.
 *   - useSynchronisedDiffScroll drives the partner pane when refA scrolls.
 *   - LoadBearingLineMarker renders one marker per (file, line) group + dedupe.
 *   - Responsive collapse: <960px renders Tabs, >=960px renders side-by-side.
 *   - Feature flag OFF (?diff_v2=0) renders the legacy stacked layout.
 *   - The imperative scrollTo({file, line}) handle scrolls both panes.
 */
import * as React from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import { buildAnchorMap, EMPTY_ANCHOR_MAP } from "@/lib/diff-anchor-map";
import { useSynchronisedDiffScroll } from "@/lib/use-synchronised-diff-scroll";
import {
  ThreeWayDiff,
  type ThreeWayDiffHandle,
} from "@/components/report/ThreeWayDiff";

// Telemetry mock — these tests assert that the synced-scroll and
// hover events fire with the right shape.
const trackMock = vi.fn();
vi.mock("@/lib/telemetry", () => ({
  track: (...args: unknown[]) => trackMock(...args),
}));

// ── Fixture diffs ───────────────────────────────────────────────────────────
// Two unified diffs over the same file (``src/session.ts``) with two hunks
// each — enough to produce a non-trivial anchor map and exercise pairing.

const USER_DIFF = `diff --git a/src/session.ts b/src/session.ts
index abc..def 100644
--- a/src/session.ts
+++ b/src/session.ts
@@ -10,5 +10,5 @@
 function readSession(cookie) {
-  if (cookie === undefined) {
+  if (!cookie) {
     return null;
   }
   return cookie["uid"];
@@ -40,3 +40,4 @@
 export function endSession(id) {
-  delete sessions[id];
+  sessions.delete(id);
+  return true;
 }
`;

const IDEAL_DIFF = `diff --git a/src/session.ts b/src/session.ts
index abc..ghi 100644
--- a/src/session.ts
+++ b/src/session.ts
@@ -10,7 +10,9 @@
 function readSession(cookie) {
-  if (cookie === undefined) {
+  if (!session || !session.isValid(Date.now())) {
     return null;
   }
+  // ensure cookie is fresh
+  validate(cookie);
   return cookie["uid"];
@@ -42,3 +44,3 @@
 export function endSession(id) {
-  delete sessions[id];
+  sessions.delete(id);
 }
`;

const AGENT_DIFF = `diff --git a/src/session.ts b/src/session.ts
index abc..xxx 100644
--- a/src/session.ts
+++ b/src/session.ts
@@ -10,3 +10,3 @@
 function readSession(cookie) {
-  if (cookie === undefined) {
+  if (cookie == null) {
     return null;
`;

// ── Helpers ────────────────────────────────────────────────────────────────

function setViewportTo(width: number): void {
  Object.defineProperty(window, "innerWidth", {
    writable: true,
    configurable: true,
    value: width,
  });
  // Drive the matchMedia mock so ``useMediaQuery`` returns the right value.
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => {
      // Only the ``max-width: 959px`` query matters here; everything else
      // defaults to false so other consumers don't surprise us.
      const match = /max-width:\s*(\d+)px/.exec(query);
      const max = match && match[1] ? Number.parseInt(match[1], 10) : null;
      const matches = max !== null && width <= max;
      return {
        matches,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      };
    }),
  });
}

function setSearch(qs: string): void {
  // jsdom's ``window.location`` is mutable via assignment to its sub-fields.
  window.history.replaceState(null, "", qs);
}

beforeEach(() => {
  trackMock.mockClear();
  setSearch("/");
  setViewportTo(1280); // desktop default
});

afterEach(() => {
  // Reset between tests so a query-string override doesn't bleed.
  setSearch("/");
});

// ── buildAnchorMap ─────────────────────────────────────────────────────────

describe("buildAnchorMap (P1-5)", () => {
  it("pairs hunk-N of A with hunk-N of B for a common file", () => {
    const map = buildAnchorMap(USER_DIFF, IDEAL_DIFF);
    expect(map.anchors.length).toBe(2);
    // First hunk anchors on the first changed line — line 10 on the new
    // side of A and B (both rewrite the ``if (cookie …)`` guard).
    const first = map.anchors[0];
    const second = map.anchors[1];
    expect(first?.file).toBe("src/session.ts");
    expect(first?.aLine).toBeGreaterThanOrEqual(10);
    expect(first?.bLine).toBeGreaterThanOrEqual(10);
    // Second hunk pairs at the ``delete sessions[id]`` rewrite around line 40+.
    expect(second?.aLine).toBeGreaterThanOrEqual(40);
    expect(second?.bLine).toBeGreaterThanOrEqual(42);
  });

  it("returns no anchors when no files are shared", () => {
    const onlyA = `diff --git a/only-in-a.ts b/only-in-a.ts
index 0..1 100644
--- a/only-in-a.ts
+++ b/only-in-a.ts
@@ -1,1 +1,1 @@
-old
+new
`;
    const onlyB = `diff --git a/only-in-b.ts b/only-in-b.ts
index 0..1 100644
--- a/only-in-b.ts
+++ b/only-in-b.ts
@@ -1,1 +1,1 @@
-old
+new
`;
    const map = buildAnchorMap(onlyA, onlyB);
    expect(map.anchors).toEqual([]);
    expect(map.bSortedIndices).toEqual([]);
  });

  it("is sorted by aLine ascending", () => {
    const map = buildAnchorMap(USER_DIFF, IDEAL_DIFF);
    for (let i = 1; i < map.anchors.length; i += 1) {
      const prev = map.anchors[i - 1];
      const cur = map.anchors[i];
      if (!prev || !cur) continue;
      expect(cur.aLine).toBeGreaterThanOrEqual(prev.aLine);
    }
  });

  it("returns a bSortedIndices view sorted by bLine ascending", () => {
    const map = buildAnchorMap(USER_DIFF, IDEAL_DIFF);
    expect(map.bSortedIndices.length).toBe(map.anchors.length);
    for (let i = 1; i < map.bSortedIndices.length; i += 1) {
      const prevIdx = map.bSortedIndices[i - 1];
      const curIdx = map.bSortedIndices[i];
      if (prevIdx === undefined || curIdx === undefined) continue;
      const prev = map.anchors[prevIdx];
      const cur = map.anchors[curIdx];
      if (!prev || !cur) continue;
      expect(cur.bLine).toBeGreaterThanOrEqual(prev.bLine);
    }
  });
});

// ── useSynchronisedDiffScroll ──────────────────────────────────────────────

describe("useSynchronisedDiffScroll (P1-5)", () => {
  /** Tiny harness that mounts two scrollable divs and wires the hook. */
  function ScrollHarness({
    onPartnerScrolled,
  }: {
    onPartnerScrolled?: (info: {
      direction: "user_to_ideal" | "ideal_to_user";
      anchorCount: number;
    }) => void;
  }): React.JSX.Element {
    const refA = React.useRef<HTMLDivElement>(null);
    const refB = React.useRef<HTMLDivElement>(null);
    const anchors = React.useMemo(
      () => buildAnchorMap(USER_DIFF, IDEAL_DIFF),
      [],
    );
    useSynchronisedDiffScroll(refA, refB, anchors, {
      pixelsPerLine: 10,
      onPartnerScrolled,
    });
    return (
      <div>
        <div
          ref={refA}
          data-testid="pane-a"
          style={{ height: 200, overflow: "auto" }}
        >
          <div style={{ height: 2000 }} />
        </div>
        <div
          ref={refB}
          data-testid="pane-b"
          style={{ height: 200, overflow: "auto" }}
        >
          <div style={{ height: 2000 }} />
        </div>
      </div>
    );
  }

  it("scrolls refB when refA scrolls", async () => {
    const onPartnerScrolled = vi.fn();
    render(<ScrollHarness onPartnerScrolled={onPartnerScrolled} />);
    const a = screen.getByTestId("pane-a");
    const b = screen.getByTestId("pane-b");
    expect(b.scrollTop).toBe(0);

    a.scrollTop = 250; // 250px / 10pxPerLine = line 25
    fireEvent.scroll(a);

    await waitFor(() => {
      expect(b.scrollTop).toBeGreaterThan(0);
    });
    expect(onPartnerScrolled).toHaveBeenCalledWith(
      expect.objectContaining({ direction: "user_to_ideal", anchorCount: 2 }),
    );
  });

  it("avoids the A→B→A cycle via the tick guard", async () => {
    const onPartnerScrolled = vi.fn();
    render(<ScrollHarness onPartnerScrolled={onPartnerScrolled} />);
    const a = screen.getByTestId("pane-a");
    const b = screen.getByTestId("pane-b");

    a.scrollTop = 300;
    fireEvent.scroll(a);
    await waitFor(() => expect(onPartnerScrolled).toHaveBeenCalledTimes(1));

    // The synthetic B scroll the hook just dispatched should NOT bounce
    // back as a user_to_ideal cycle.
    fireEvent.scroll(b);
    // Give rAF a tick to settle.
    await new Promise((resolve) => setTimeout(resolve, 30));
    const calls = onPartnerScrolled.mock.calls.length;
    expect(calls).toBe(1);
  });

  it("no-ops when anchor map is empty", async () => {
    function EmptyAnchorHarness(): React.JSX.Element {
      const refA = React.useRef<HTMLDivElement>(null);
      const refB = React.useRef<HTMLDivElement>(null);
      useSynchronisedDiffScroll(refA, refB, EMPTY_ANCHOR_MAP, {
        pixelsPerLine: 10,
      });
      return (
        <div>
          <div ref={refA} data-testid="empty-a" style={{ height: 200, overflow: "auto" }}>
            <div style={{ height: 2000 }} />
          </div>
          <div ref={refB} data-testid="empty-b" style={{ height: 200, overflow: "auto" }}>
            <div style={{ height: 2000 }} />
          </div>
        </div>
      );
    }
    render(<EmptyAnchorHarness />);
    const a = screen.getByTestId("empty-a");
    const b = screen.getByTestId("empty-b");
    a.scrollTop = 400;
    fireEvent.scroll(a);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(b.scrollTop).toBe(0);
  });
});

// ── ThreeWayDiff component ─────────────────────────────────────────────────

describe("ThreeWayDiff (P1-5)", () => {
  it("renders side-by-side on desktop viewports", () => {
    setViewportTo(1280);
    render(
      <ThreeWayDiff
        userDiff={USER_DIFF}
        idealDiff={IDEAL_DIFF}
        agentPatchDiff={AGENT_DIFF}
      />,
    );
    const root = screen.getByTestId("three-way-diff");
    expect(root).toHaveAttribute("data-layout", "side-by-side");
    expect(screen.getByTestId("three-way-diff-pane-user")).toBeInTheDocument();
    expect(screen.getByTestId("three-way-diff-pane-ideal")).toBeInTheDocument();
  });

  it("renders the mobile tabs layout under 960px", () => {
    setViewportTo(640);
    render(
      <ThreeWayDiff
        userDiff={USER_DIFF}
        idealDiff={IDEAL_DIFF}
        agentPatchDiff={AGENT_DIFF}
      />,
    );
    const root = screen.getByTestId("three-way-diff");
    expect(root).toHaveAttribute("data-layout", "tabs");
    expect(
      screen.getByRole("tab", { name: /your submission/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /ideal solution/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /agent's original patch/i }),
    ).toBeInTheDocument();
  });

  it("renders the legacy stacked layout when feature flag is OFF", () => {
    setSearch("/?diff_v2=0");
    render(
      <ThreeWayDiff
        userDiff={USER_DIFF}
        idealDiff={IDEAL_DIFF}
        agentPatchDiff={AGENT_DIFF}
      />,
    );
    const root = screen.getByTestId("three-way-diff");
    expect(root).toHaveAttribute("data-layout", "legacy");
    // Legacy layout doesn't expose the scroll-sync container ids.
    expect(screen.queryByTestId("three-way-diff-pane-user")).toBeNull();
  });

  it("renders a load-bearing marker at the moment's line", async () => {
    setViewportTo(1280);
    render(
      <ThreeWayDiff
        userDiff={USER_DIFF}
        idealDiff={IDEAL_DIFF}
        agentPatchDiff={null}
        criticalMoments={[
          {
            event_id: 42,
            file_path: "src/session.ts",
            start_line: 10,
            label: "the line the agent got wrong",
          },
        ]}
      />,
    );
    // The marker queries via diff-{file}-i{line} / -n / -d ids that
    // DiffViewer stamps. Wait for it to appear on the user pane.
    await waitFor(() => {
      const markers = screen.queryAllByTestId("load-bearing-marker-user");
      expect(markers.length).toBeGreaterThan(0);
    });
    const userMarkers = screen.getAllByTestId("load-bearing-marker-user");
    expect(userMarkers[0]).toHaveAttribute("data-event-id", "42");
    expect(userMarkers[0]).toHaveAttribute("data-group-size", "1");
  });

  it("dedupes multiple moments on the same line into one chip", async () => {
    setViewportTo(1280);
    render(
      <ThreeWayDiff
        userDiff={USER_DIFF}
        idealDiff={IDEAL_DIFF}
        agentPatchDiff={null}
        criticalMoments={[
          {
            event_id: 1,
            file_path: "src/session.ts",
            start_line: 10,
            label: "moment 1",
          },
          {
            event_id: 2,
            file_path: "src/session.ts",
            start_line: 10,
            label: "moment 2",
          },
        ]}
      />,
    );
    await waitFor(() => {
      const markers = screen.queryAllByTestId("load-bearing-marker-user");
      expect(markers.length).toBe(1);
    });
    const marker = screen.getAllByTestId("load-bearing-marker-user")[0];
    expect(marker).toHaveAttribute("data-group-size", "2");
  });

  it("exposes a scrollTo(file, line) imperative method that scrolls both panes", () => {
    setViewportTo(1280);
    const ref = React.createRef<ThreeWayDiffHandle>();
    render(
      <ThreeWayDiff
        ref={ref}
        userDiff={USER_DIFF}
        idealDiff={IDEAL_DIFF}
        agentPatchDiff={null}
      />,
    );
    const userPane = screen.getByTestId("three-way-diff-pane-user");
    const idealPane = screen.getByTestId("three-way-diff-pane-ideal");

    // jsdom's ``scrollTop`` setter no-ops on elements that aren't computed
    // scrollable; redefine the property with a plain numeric backing field
    // so we can assert the imperative handle's write landed.
    const installScrollTop = (el: HTMLElement) => {
      let value = 0;
      Object.defineProperty(el, "scrollTop", {
        configurable: true,
        get() {
          return value;
        },
        set(v: number) {
          value = v;
        },
      });
    };
    installScrollTop(userPane);
    installScrollTop(idealPane);

    // Force a deterministic getBoundingClientRect so the scroll math is
    // testable in jsdom (which returns zeros by default).
    const paneRect = { top: 0, left: 0, right: 100, bottom: 200, width: 100, height: 200, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
    const anchorRect = { top: 80, left: 0, right: 100, bottom: 100, width: 100, height: 20, x: 0, y: 80, toJSON: () => ({}) } as DOMRect;
    const stub = vi.fn().mockReturnValue(paneRect);
    userPane.getBoundingClientRect = stub;
    idealPane.getBoundingClientRect = stub;

    // Stub the anchor lookup target so the imperative handle finds an
    // element. Use a synthetic file path that the real DiffViewer would
    // never emit — otherwise the diff renderer's stamped anchor (with a
    // zero bounding rect under jsdom) shadows ours.
    const userAnchor = document.createElement("span");
    userAnchor.id = "diff-virtual/path.ts-n10";
    userAnchor.getBoundingClientRect = () => anchorRect;
    userPane.appendChild(userAnchor);
    const idealAnchor = document.createElement("span");
    idealAnchor.id = "diff-virtual/path.ts-n10";
    idealAnchor.getBoundingClientRect = () => anchorRect;
    idealPane.appendChild(idealAnchor);

    // Sanity: the anchor element is findable from the pane.
    expect(
      userPane.querySelector('[id="diff-virtual/path.ts-n10"]'),
    ).toBe(userAnchor);

    act(() => {
      ref.current?.scrollTo("virtual/path.ts", 10);
    });

    // Both panes scrolled by the anchor offset (80) - the pane top (0).
    expect(userPane.scrollTop).toBe(80);
    expect(idealPane.scrollTop).toBe(80);
  });

  it("suppresses the marker when the moment has no line anchor", () => {
    setViewportTo(1280);
    render(
      <ThreeWayDiff
        userDiff={USER_DIFF}
        idealDiff={IDEAL_DIFF}
        agentPatchDiff={null}
        criticalMoments={[
          {
            event_id: 99,
            label: "no line — submitted without verification",
          },
        ]}
      />,
    );
    expect(screen.queryAllByTestId("load-bearing-marker-user").length).toBe(0);
  });
});
