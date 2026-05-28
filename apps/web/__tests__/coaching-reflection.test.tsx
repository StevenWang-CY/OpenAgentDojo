/**
 * P1-4 — CoachingReflection component tests.
 *
 * Covers:
 *   - The section renders nothing when ``reflection`` is null (the BE
 *     returned a null body — opt-out / no notes / 503 normalised).
 *   - Inline ``[event:N]`` and ``[note:"..."]`` markers are split into
 *     clickable anchors.
 *   - Clicking an event anchor calls ``onScrollToEvent`` with the
 *     parsed event id; clicking a note anchor calls
 *     ``onScrollToScratchpad``.
 *   - ``coaching_reflection_shown`` fires exactly once on first
 *     render of a non-null reflection.
 *   - The request fires lazily — the component does NOT call the API
 *     until the section enters the viewport (we drive the
 *     IntersectionObserver manually via a global mock).
 */
import * as React from "react";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { API_BASE, server } from "./contract/_setup";
import {
  CoachingReflection,
  parseAnchorSegments,
} from "@/components/report/CoachingReflection";

const SUBMISSION_ID = "11111111-2222-3333-4444-555555555555";

// Telemetry — captured per-test.
const trackMock = vi.fn();
vi.mock("@/lib/telemetry", () => ({
  track: (...args: unknown[]) => trackMock(...args),
}));

// IntersectionObserver mock. Each component instance receives a fresh
// observer; we stash the callback on the module so tests can drive
// "the section just became visible" deterministically.
type IOCallback = (
  entries: ReadonlyArray<{ isIntersecting: boolean }>,
  observer: unknown,
) => void;
const ioCallbacks: IOCallback[] = [];

class MockIntersectionObserver {
  constructor(cb: IOCallback) {
    ioCallbacks.push(cb);
  }
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  takeRecords = vi.fn(() => []);
}

beforeAll(() => {
  // jsdom doesn't ship IntersectionObserver; install our deterministic
  // shim once per file.
  (
    globalThis as unknown as { IntersectionObserver: typeof MockIntersectionObserver }
  ).IntersectionObserver = MockIntersectionObserver;
  (
    globalThis as unknown as { window: { IntersectionObserver: typeof MockIntersectionObserver } }
  ).window.IntersectionObserver = MockIntersectionObserver;
});

beforeEach(() => {
  trackMock.mockClear();
  ioCallbacks.length = 0;
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  server.resetHandlers();
  server.close();
});

/** Trigger every registered IntersectionObserver callback as
 *  ``isIntersecting=true``. Mirrors the user scrolling the section
 *  into the viewport. Wrapped in ``act`` so React's state-update
 *  warnings stay quiet — the observer's callback flips a useState
 *  bit that the component reacts to. */
function triggerIntersection() {
  act(() => {
    for (const cb of ioCallbacks) {
      cb([{ isIntersecting: true }], null);
    }
  });
}

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

function mockCoaching(body: Record<string, unknown>, status = 200): void {
  server.use(
    http.get(
      `${API_BASE}/api/v1/submissions/${SUBMISSION_ID}/coaching`,
      () => HttpResponse.json(body, { status }),
    ),
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("parseAnchorSegments", () => {
  it("splits text on event + note markers", () => {
    const out = parseAnchorSegments(
      'At 00:02 you asked [event:7] but your notes said [note:"check cookies"] earlier.',
    );
    expect(out).toEqual([
      { kind: "text", value: "At 00:02 you asked " },
      { kind: "event", value: "7" },
      { kind: "text", value: " but your notes said " },
      { kind: "note_quote", value: "check cookies" },
      { kind: "text", value: " earlier." },
    ]);
  });

  it("returns a single text segment when no markers are present", () => {
    const out = parseAnchorSegments("just plain prose, no markers");
    expect(out).toEqual([
      { kind: "text", value: "just plain prose, no markers" },
    ]);
  });
});

describe("CoachingReflection", () => {
  it("renders nothing when the reflection is null", async () => {
    mockCoaching({
      reflection: null,
      anchored_event_id: null,
      anchored_note_quote: null,
      cached: false,
      generated_at: new Date().toISOString(),
    });

    renderWithQuery(
      <CoachingReflection
        submissionId={SUBMISSION_ID}
        onScrollToEvent={vi.fn()}
        onScrollToScratchpad={vi.fn()}
      />,
    );

    triggerIntersection();

    // Body never lands — the section silently collapses.
    await waitFor(() => {
      const body = screen.queryByTestId("coaching-reflection-body");
      expect(body).toBeNull();
    });
    // Telemetry must NOT fire on a null reflection.
    expect(
      trackMock.mock.calls.find(
        ([event]) => event === "coaching_reflection_shown",
      ),
    ).toBeUndefined();
  });

  it("renders text with event + note anchors parsed", async () => {
    mockCoaching({
      reflection:
        'At 00:01 you sent [event:42] a prompt but flagged [note:"cookie expiry"] in your notes.',
      anchored_event_id: 42,
      anchored_note_quote: "cookie expiry",
      cached: true,
      generated_at: new Date().toISOString(),
    });

    renderWithQuery(
      <CoachingReflection
        submissionId={SUBMISSION_ID}
        onScrollToEvent={vi.fn()}
        onScrollToScratchpad={vi.fn()}
      />,
    );

    triggerIntersection();

    const eventAnchor = await screen.findByTestId(
      "coaching-anchor-event",
    );
    expect(eventAnchor).toHaveAttribute("data-event-id", "42");

    const noteAnchor = screen.getByTestId("coaching-anchor-note");
    expect(noteAnchor).toHaveAttribute("data-note-quote", "cookie expiry");
  });

  it("fires onScrollToEvent with the parsed id when an event anchor is clicked", async () => {
    mockCoaching({
      reflection: "Look at [event:99] for the issue.",
      anchored_event_id: 99,
      anchored_note_quote: null,
      cached: false,
      generated_at: new Date().toISOString(),
    });

    const onScrollToEvent = vi.fn();
    const onScrollToScratchpad = vi.fn();

    renderWithQuery(
      <CoachingReflection
        submissionId={SUBMISSION_ID}
        onScrollToEvent={onScrollToEvent}
        onScrollToScratchpad={onScrollToScratchpad}
      />,
    );

    triggerIntersection();

    const anchor = await screen.findByTestId("coaching-anchor-event");
    fireEvent.click(anchor);

    expect(onScrollToEvent).toHaveBeenCalledWith(99);
    expect(onScrollToScratchpad).not.toHaveBeenCalled();
    expect(
      trackMock.mock.calls.find(
        ([event, props]) =>
          event === "coaching_reflection_anchor_clicked" &&
          (props as { anchor_kind?: string })?.anchor_kind === "timeline",
      ),
    ).toBeDefined();
  });

  it("fires onScrollToScratchpad when a note anchor is clicked", async () => {
    mockCoaching({
      reflection: 'Your note said [note:"reproduce first"] but you didn\'t.',
      anchored_event_id: null,
      anchored_note_quote: "reproduce first",
      cached: false,
      generated_at: new Date().toISOString(),
    });

    const onScrollToEvent = vi.fn();
    const onScrollToScratchpad = vi.fn();

    renderWithQuery(
      <CoachingReflection
        submissionId={SUBMISSION_ID}
        onScrollToEvent={onScrollToEvent}
        onScrollToScratchpad={onScrollToScratchpad}
      />,
    );

    triggerIntersection();

    const anchor = await screen.findByTestId("coaching-anchor-note");
    fireEvent.click(anchor);

    expect(onScrollToScratchpad).toHaveBeenCalledTimes(1);
    expect(onScrollToEvent).not.toHaveBeenCalled();
  });

  it("fires coaching_reflection_shown exactly once on first render", async () => {
    mockCoaching({
      reflection: "Hello [event:1] world.",
      anchored_event_id: 1,
      anchored_note_quote: null,
      cached: true,
      generated_at: new Date().toISOString(),
    });

    renderWithQuery(
      <CoachingReflection
        submissionId={SUBMISSION_ID}
        onScrollToEvent={vi.fn()}
        onScrollToScratchpad={vi.fn()}
      />,
    );

    triggerIntersection();

    await waitFor(() => {
      expect(
        trackMock.mock.calls.filter(
          ([event]) => event === "coaching_reflection_shown",
        ),
      ).toHaveLength(1);
    });
    // The single call must carry the cached flag from the payload.
    const shown = trackMock.mock.calls.find(
      ([event]) => event === "coaching_reflection_shown",
    );
    expect(shown?.[1]).toMatchObject({
      submission_id: SUBMISSION_ID,
      cached: true,
    });
  });

  it("lazy-fetches: no network call until the section is visible", async () => {
    const seen: string[] = [];
    server.use(
      http.get(
        `${API_BASE}/api/v1/submissions/${SUBMISSION_ID}/coaching`,
        ({ request }) => {
          seen.push(request.url);
          return HttpResponse.json({
            reflection: "Hi.",
            anchored_event_id: null,
            anchored_note_quote: null,
            cached: false,
            generated_at: new Date().toISOString(),
          });
        },
      ),
    );

    renderWithQuery(
      <CoachingReflection
        submissionId={SUBMISSION_ID}
        onScrollToEvent={vi.fn()}
        onScrollToScratchpad={vi.fn()}
      />,
    );

    // Nothing has fired yet — the section is in skeleton state.
    expect(seen).toHaveLength(0);

    triggerIntersection();

    await waitFor(() => expect(seen.length).toBe(1));
  });

  it("hides the section silently on 503 llm_unavailable", async () => {
    server.use(
      http.get(
        `${API_BASE}/api/v1/submissions/${SUBMISSION_ID}/coaching`,
        () =>
          HttpResponse.json(
            { code: "llm_unavailable", message: "coaching unavailable" },
            { status: 503 },
          ),
      ),
    );

    renderWithQuery(
      <CoachingReflection
        submissionId={SUBMISSION_ID}
        onScrollToEvent={vi.fn()}
        onScrollToScratchpad={vi.fn()}
      />,
    );

    triggerIntersection();

    await waitFor(() => {
      expect(screen.queryByTestId("coaching-reflection-body")).toBeNull();
    });
    expect(
      trackMock.mock.calls.find(
        ([event]) => event === "coaching_reflection_shown",
      ),
    ).toBeUndefined();
  });
});
