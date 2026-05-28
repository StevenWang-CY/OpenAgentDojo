/**
 * P1-4 FE remediation — CoachingReflection 503 envelope path.
 *
 * The backend's coaching endpoint returns 503 ``{detail: {code:
 * "llm_unavailable", message: ...}}`` when AWS Bedrock is unreachable
 * and no cached row exists. The original FE only looked at the flat
 * ``err.body.code`` field, which meant the FastAPI HTTPException
 * envelope (``detail`` wraps the code) silently re-threw the error and
 * the section vanished without an explanation — indistinguishable from
 * the legitimate "user opted out" path.
 *
 * This suite locks in:
 *   1. The FE handles BOTH wire shapes (the FastAPI ``detail.code``
 *      form and the flat ``code`` form) for the 503 ``llm_unavailable``
 *      response.
 *   2. On 503 the section renders the dedicated "coaching unavailable
 *      — Bedrock is offline" copy via the
 *      ``coaching-reflection-unavailable`` testid — distinct from the
 *      silent hide we use for opt-outs.
 *   3. ``coaching_reflection_shown`` does NOT fire on the 503 path.
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
import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { API_BASE, server } from "./contract/_setup";
import { CoachingReflection } from "@/components/report/CoachingReflection";

const SUBMISSION_ID = "deadbeef-1234-5678-9abc-def012345678";

const trackMock = vi.fn();
vi.mock("@/lib/telemetry", () => ({
  track: (...args: unknown[]) => trackMock(...args),
}));

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

function triggerIntersection(): void {
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

describe("CoachingReflection — 503 envelope handling (FE remediation)", () => {
  it("renders the unavailable copy on the FastAPI {detail: {code}} envelope", async () => {
    server.use(
      http.get(
        `${API_BASE}/api/v1/submissions/${SUBMISSION_ID}/coaching`,
        () =>
          HttpResponse.json(
            {
              detail: {
                code: "llm_unavailable",
                message: "Bedrock is offline.",
              },
            },
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

    // The unavailable copy lands.
    const copy = await screen.findByTestId(
      "coaching-reflection-unavailable-copy",
    );
    expect(copy.textContent ?? "").toMatch(/coaching unavailable/i);
    expect(copy.textContent ?? "").toMatch(/Bedrock is offline/i);

    // The "happy body" testid must NOT render.
    expect(screen.queryByTestId("coaching-reflection-body")).toBeNull();

    // Telemetry: the shown event must NOT fire on the unavailable path —
    // the funnel splits "section seen" from "section degraded".
    expect(
      trackMock.mock.calls.find(
        ([event]) => event === "coaching_reflection_shown",
      ),
    ).toBeUndefined();
  });

  it("renders the unavailable copy on the flat {code} envelope", async () => {
    // Legacy / hand-rolled endpoints may emit the flat shape; the FE
    // resolves both to the same null-payload sentinel so the user-facing
    // wording stays consistent regardless of which wire shape the backend
    // happens to serve.
    server.use(
      http.get(
        `${API_BASE}/api/v1/submissions/${SUBMISSION_ID}/coaching`,
        () =>
          HttpResponse.json(
            { code: "llm_unavailable", message: "Bedrock offline" },
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
      expect(
        screen.getByTestId("coaching-reflection-unavailable-copy"),
      ).toBeInTheDocument();
    });
  });

  it("stays silently hidden when reflection is null without a 503 reason", async () => {
    // The opt-out path returns 200 with ``reflection: null`` and no
    // ``unavailable_reason`` — the dedicated copy must NOT render in this
    // case, only the silent hide.
    server.use(
      http.get(
        `${API_BASE}/api/v1/submissions/${SUBMISSION_ID}/coaching`,
        () =>
          HttpResponse.json(
            {
              reflection: null,
              anchored_event_id: null,
              anchored_note_quote: null,
              cached: false,
              generated_at: new Date().toISOString(),
            },
            { status: 200 },
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

    // Give the query a tick to settle, then verify the silent hide.
    await waitFor(() => {
      expect(
        screen.queryByTestId("coaching-reflection-unavailable-copy"),
      ).toBeNull();
      expect(screen.queryByTestId("coaching-reflection-body")).toBeNull();
    });
  });
});
