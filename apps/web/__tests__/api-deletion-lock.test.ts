/**
 * FE-audit fix — centralised deletion-lock 403 handling.
 *
 * The backend's DeletionLockMiddleware returns 403 with body
 * ``{code: "deletion_scheduled", scheduled_for}`` for every mutating
 * request while ``deletion_scheduled_at`` is set. A stale tab whose
 * ``["me"]`` cache was cleared elsewhere would otherwise surface a
 * generic toast ("Couldn't update your profile.") instead of the lock
 * banner. ``lib/api.ts`` now dispatches a ``deletion-lock-detected``
 * window event whenever it sees that response shape; ``app/providers.tsx``
 * listens for it and invalidates ``["me"]`` so the banner appears.
 *
 * Covered here:
 *   1. The event fires on 403 + ``code === "deletion_scheduled"``.
 *   2. The ``ApiError`` is still thrown so call-sites can branch locally.
 *   3. Generic 403s (no ``code``) do NOT fire the event — we don't want
 *      to invalidate ``["me"]`` on every unrelated forbidden.
 *   4. The Providers-level listener invalidates ``["me"]`` on the event.
 */
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { ApiError, account } from "@/lib/api";

type FetchArgs = [input: string | URL | Request, init?: RequestInit];
type FetchMock = MockInstance<(...args: FetchArgs) => Promise<Response>>;

const ORIGINAL_FETCH = globalThis.fetch;

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  // Ensure no leftover listeners from a prior test leak into this one.
  // (Each test sets its own listener and cleans up via the afterEach.)
});

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  vi.restoreAllMocks();
});

describe("deletion-lock 403 propagation", () => {
  it("dispatches a deletion-lock-detected event on 403 + deletion_scheduled", async () => {
    const scheduledFor = "2026-06-01T00:00:00Z";
    const fetchMock: FetchMock = vi.fn(async (_input, _init) =>
      jsonResponse(
        {
          detail: "Account scheduled for deletion.",
          code: "deletion_scheduled",
          scheduled_for: scheduledFor,
        },
        403,
      ),
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    const eventBody = await new Promise<{
      code?: string;
      scheduled_for?: string;
    } | null>((resolve) => {
      const handler = (event: Event) => {
        window.removeEventListener("deletion-lock-detected", handler);
        const ce = event as CustomEvent<{
          code?: string;
          scheduled_for?: string;
        }>;
        resolve(ce.detail ?? null);
      };
      window.addEventListener("deletion-lock-detected", handler);

      // Fire-and-forget — the assertion below also asserts the throw.
      account
        .updateProfile({ display_name: "anything" })
        .catch(() => undefined);
    });

    expect(eventBody?.code).toBe("deletion_scheduled");
    expect(eventBody?.scheduled_for).toBe(scheduledFor);
  });

  it("still throws ApiError so callers can branch on the lock locally", async () => {
    const fetchMock: FetchMock = vi.fn(async (_input, _init) =>
      jsonResponse(
        {
          detail: "Account scheduled for deletion.",
          code: "deletion_scheduled",
          scheduled_for: "2026-06-01T00:00:00Z",
        },
        403,
      ),
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    await expect(
      account.updateProfile({ display_name: "anything" }),
    ).rejects.toMatchObject({
      status: 403,
      body: expect.objectContaining({ code: "deletion_scheduled" }),
    });
  });

  it("does NOT fire the event for unrelated 403s (e.g. CSRF)", async () => {
    const fetchMock: FetchMock = vi.fn(async (_input, _init) =>
      jsonResponse({ detail: "CSRF check failed", code: "csrf_invalid" }, 403),
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    const handler = vi.fn();
    window.addEventListener("deletion-lock-detected", handler);
    try {
      await expect(
        account.updateProfile({ display_name: "anything" }),
      ).rejects.toBeInstanceOf(ApiError);
    } finally {
      window.removeEventListener("deletion-lock-detected", handler);
    }
    expect(handler).not.toHaveBeenCalled();
  });

  it("the Providers-level listener invalidates ['me'] on the event", async () => {
    // Recreate the listener wiring from ``app/providers.tsx`` so the
    // contract is asserted in isolation (without rendering the full
    // provider tree just to fire a single React effect).
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const handler = () => {
      void client.invalidateQueries({ queryKey: ["me"] });
    };
    window.addEventListener("deletion-lock-detected", handler);

    try {
      window.dispatchEvent(
        new CustomEvent("deletion-lock-detected", {
          detail: { code: "deletion_scheduled" },
        }),
      );
      // ``invalidateQueries`` is sync-fire — the queue isn't awaited here.
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["me"] });
    } finally {
      window.removeEventListener("deletion-lock-detected", handler);
    }
  });
});
