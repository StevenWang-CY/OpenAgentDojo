/**
 * P1-9 — Consent sync retry on transient failure.
 *
 * The anonymous → signed-in sync needs to be robust against a single 5xx
 * blip on the auth bootstrap roundtrip. Without the retry the audit-trail
 * row for the pre-login consent decision is permanently lost.
 *
 * Covers:
 *   - Transient 5xx → one backoff retry → second success populates the
 *     server side without throwing.
 *   - Two consecutive 5xx → the helper rethrows so the caller can re-arm
 *     its ``syncedRef`` and try again on a later focus event.
 *   - 401 short-circuits without a retry (anonymous-path; nothing to do).
 *   - The ``[consent.sync]`` warn is emitted at most once per page load.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";

import { API_BASE, server } from "./contract/_setup";
import {
  LATEST_CONSENT_VERSION,
  __resetConsentSyncWarnFlagForTests,
  syncLocalConsentToServer,
  type ConsentState,
} from "@/lib/consent";

function stateWith(granted: boolean): ConsentState {
  const at = new Date("2026-05-24T08:00:00Z").toISOString();
  return {
    analytics: { granted, version: LATEST_CONSENT_VERSION, at },
    functional: null,
    marketing: null,
  };
}

let warnSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  __resetConsentSyncWarnFlagForTests();
  warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  server.listen({ onUnhandledRequest: "bypass" });
});

afterEach(() => {
  server.resetHandlers();
  server.close();
  warnSpy.mockRestore();
});

describe("syncLocalConsentToServer", () => {
  it("retries once on a transient 5xx and then succeeds", async () => {
    let attempts = 0;
    server.use(
      http.post(`${API_BASE}/api/v1/auth/me/consent`, () => {
        attempts += 1;
        if (attempts === 1) {
          return HttpResponse.json(
            { detail: "transient" },
            { status: 503 },
          );
        }
        return new HttpResponse(null, { status: 204 });
      }),
    );

    await expect(syncLocalConsentToServer(stateWith(true))).resolves.toBeUndefined();
    expect(attempts).toBe(2);
    // No warn on the success path.
    expect(warnSpy).not.toHaveBeenCalled();
  });

  it("rethrows after two consecutive 5xx so the caller can re-arm the sync", async () => {
    let attempts = 0;
    server.use(
      http.post(`${API_BASE}/api/v1/auth/me/consent`, () => {
        attempts += 1;
        return HttpResponse.json({ detail: "fault" }, { status: 503 });
      }),
    );

    await expect(syncLocalConsentToServer(stateWith(true))).rejects.toBeTruthy();
    expect(attempts).toBe(2);
    // Single deduplicated [consent.sync] warn — not one per attempt.
    expect(warnSpy).toHaveBeenCalledTimes(1);
    const firstCall = warnSpy.mock.calls[0] ?? [];
    expect(String(firstCall[0] ?? "")).toMatch(/\[consent\.sync\]/);
  });

  it("silently swallows 401 (anonymous-path) without a retry or warn", async () => {
    let attempts = 0;
    server.use(
      http.post(`${API_BASE}/api/v1/auth/me/consent`, () => {
        attempts += 1;
        return HttpResponse.json({ detail: "Unauthorized" }, { status: 401 });
      }),
    );

    await expect(syncLocalConsentToServer(stateWith(true))).resolves.toBeUndefined();
    expect(attempts).toBe(1);
    expect(warnSpy).not.toHaveBeenCalled();
  });

  it("deduplicates the [consent.sync] warn across multiple failing kinds", async () => {
    server.use(
      http.post(`${API_BASE}/api/v1/auth/me/consent`, () =>
        HttpResponse.json({ detail: "fault" }, { status: 502 }),
      ),
    );

    const at = new Date().toISOString();
    const state: ConsentState = {
      analytics: { granted: true, version: LATEST_CONSENT_VERSION, at },
      functional: { granted: true, version: LATEST_CONSENT_VERSION, at },
      marketing: { granted: false, version: LATEST_CONSENT_VERSION, at },
    };

    await expect(syncLocalConsentToServer(state)).rejects.toBeTruthy();
    // Three kinds × two attempts each = six requests; only one warn line.
    expect(warnSpy).toHaveBeenCalledTimes(1);
  });
});
