/**
 * Audit Item A1 — toggling the coaching reflection consent off must
 * invalidate the cached ``["coaching-reflection"]`` queries so any
 * already-mounted ``CoachingReflection`` section refetches against the
 * backend (which now serves ``reflection=null``) and silently
 * unmounts.
 *
 * The test mounts both panels under the same ``QueryClientProvider`` so
 * the cache they share is the same instance the mutation invalidates.
 * Before opt-out the coaching section renders; after the toggle flips
 * to off + the next refetch lands, the section's body disappears.
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
import { PrivacyPanel } from "@/components/account/PrivacyPanel";
import { CoachingReflection } from "@/components/report/CoachingReflection";

const SUBMISSION_ID = "11111111-2222-3333-4444-555555555555";

// Telemetry mock — the assertions don't care about the calls; we just
// want a no-op so the consent + reflection emissions don't blow up.
vi.mock("@/lib/telemetry", () => ({
  track: vi.fn(),
}));

// IntersectionObserver shim — the coaching section gates fetches on
// the section becoming visible.
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
  (
    globalThis as unknown as {
      IntersectionObserver: typeof MockIntersectionObserver;
    }
  ).IntersectionObserver = MockIntersectionObserver;
  (
    globalThis as unknown as {
      window: { IntersectionObserver: typeof MockIntersectionObserver };
    }
  ).window.IntersectionObserver = MockIntersectionObserver;
});

function triggerIntersection() {
  act(() => {
    for (const cb of ioCallbacks) {
      cb([{ isIntersecting: true }], null);
    }
  });
}

beforeEach(() => {
  ioCallbacks.length = 0;
  server.listen({ onUnhandledRequest: "bypass" });
});

afterEach(() => {
  server.resetHandlers();
  server.close();
});

describe("PrivacyPanel × CoachingReflection (audit A1)", () => {
  it("opting out invalidates and refetches the cached coaching reflection", async () => {
    // Track how many GETs land so we can prove the invalidation
    // refired the query.
    let coachingFetchCount = 0;
    let optedOut = false;

    server.use(
      // Auth probe — start with the user opted IN so the panel renders
      // the toggle in the "on" position.
      http.get(`${API_BASE}/api/v1/auth/me/coaching-consent`, () =>
        HttpResponse.json({ coaching_reflections_enabled: true }),
      ),
      http.get(`${API_BASE}/api/v1/auth/me/consent`, () =>
        HttpResponse.json({}),
      ),
      // POST flips the column. The handler captures the new value so
      // the subsequent GET on the coaching endpoint serves the correct
      // payload.
      http.post(
        `${API_BASE}/api/v1/auth/me/coaching-consent`,
        async ({ request }) => {
          const body = (await request.json()) as {
            coaching_reflections_enabled: boolean;
          };
          optedOut = body.coaching_reflections_enabled === false;
          return HttpResponse.json({
            coaching_reflections_enabled: body.coaching_reflections_enabled,
          });
        },
      ),
      // Coaching reflection — serves a real body on the first call,
      // then null once the user has opted out (mirrors BE behaviour
      // wired in by 4.1).
      http.get(
        `${API_BASE}/api/v1/submissions/${SUBMISSION_ID}/coaching`,
        () => {
          coachingFetchCount += 1;
          if (optedOut) {
            return HttpResponse.json({
              reflection: null,
              anchored_event_id: null,
              anchored_note_quote: null,
              cached: false,
              generated_at: new Date().toISOString(),
            });
          }
          return HttpResponse.json({
            reflection: "you wrote X but did Y.",
            anchored_event_id: null,
            anchored_note_quote: null,
            cached: true,
            generated_at: new Date().toISOString(),
          });
        },
      ),
    );

    // Single QueryClient shared by both components so the invalidation
    // chain is end-to-end.
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={client}>
        <CoachingReflection
          submissionId={SUBMISSION_ID}
          onScrollToEvent={vi.fn()}
          onScrollToScratchpad={vi.fn()}
        />
        <PrivacyPanel />
      </QueryClientProvider>,
    );

    // 1) Coaching section becomes visible → first GET → body lands.
    triggerIntersection();
    await waitFor(() =>
      expect(screen.queryByTestId("coaching-reflection-body")).not.toBeNull(),
    );
    expect(coachingFetchCount).toBe(1);

    // 2) Toggle the coaching consent switch off.
    const toggle = await screen.findByTestId("consent-toggle-coaching");
    fireEvent.click(toggle);

    // 3) After the mutation lands, the cached coaching query is
    //    invalidated; the refetch sees ``reflection=null`` and the
    //    body disappears.
    await waitFor(() => expect(coachingFetchCount).toBeGreaterThanOrEqual(2));
    await waitFor(() =>
      expect(screen.queryByTestId("coaching-reflection-body")).toBeNull(),
    );
  });
});
