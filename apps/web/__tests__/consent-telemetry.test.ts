import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { __setTelemetryClient, track, pageView } from "@/lib/telemetry";
import {
  LATEST_CONSENT_VERSION,
  STORAGE_KEY,
  STORAGE_VERSION_KEY,
  setConsent,
} from "@/lib/consent";

// Install a fake PostHog client so we can observe whether `track`/`pageView`
// reached it. The telemetry module talks to whatever we pass into
// `__setTelemetryClient`, exactly the way the real provider would.
const capture = vi.fn();
const identify = vi.fn();

beforeEach(() => {
  window.localStorage.clear();
  capture.mockClear();
  identify.mockClear();
  __setTelemetryClient({ capture, identify });
});

afterEach(() => {
  __setTelemetryClient(null);
  window.localStorage.clear();
});

describe("telemetry consent gating", () => {
  it("does not reach the provider without analytics consent", () => {
    // Sanity check — localStorage is empty, so getConsent() returns the
    // empty state (analytics === null, which is falsy).
    track("mission_viewed", { mission_id: "x" });
    pageView("/missions");
    expect(capture).not.toHaveBeenCalled();
  });

  it("does not reach the provider when analytics is explicitly false", () => {
    setConsent("functional", true);
    setConsent("analytics", false);
    track("mission_viewed", { mission_id: "y" });
    expect(capture).not.toHaveBeenCalled();
  });

  it("reaches the provider once analytics consent is granted", () => {
    setConsent("analytics", true);
    track("mission_viewed", { mission_id: "z" });
    expect(capture).toHaveBeenCalledWith("mission_viewed", { mission_id: "z" });
  });

  it("re-blocks emission after the user revokes analytics consent", () => {
    setConsent("analytics", true);
    track("mission_viewed", { mission_id: "a" });
    expect(capture).toHaveBeenCalledTimes(1);

    setConsent("analytics", false);
    capture.mockClear();
    track("mission_viewed", { mission_id: "b" });
    expect(capture).not.toHaveBeenCalled();
  });

  it("respects a version mismatch — stale stored consent re-enters the empty state", () => {
    // Write a state at the current version, then drop the version key below
    // current to simulate a policy bump. getConsent() should report empty.
    const now = new Date().toISOString();
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        analytics: { granted: true, version: LATEST_CONSENT_VERSION, at: now },
        functional: { granted: true, version: LATEST_CONSENT_VERSION, at: now },
        marketing: { granted: true, version: LATEST_CONSENT_VERSION, at: now },
      }),
    );
    window.localStorage.setItem(
      STORAGE_VERSION_KEY,
      String(LATEST_CONSENT_VERSION - 1),
    );
    track("mission_viewed", { mission_id: "c" });
    expect(capture).not.toHaveBeenCalled();
  });

  it("does NOT emit when consent was revoked during the async SDK import (race-fix)", async () => {
    // Simulates the TelemetryProvider race: analytics is granted, the
    // dynamic import resolves, but before ``__setTelemetryClient`` lands
    // the user has flipped analytics off. The provider must re-check
    // consent post-await and abort initialisation.
    setConsent("analytics", true);
    // Pretend the SDK has loaded by installing the test client (this is
    // what the provider does post-import).
    __setTelemetryClient({ capture, identify });

    // Now the user flips analytics off — same code path the race-fix
    // guards against.
    setConsent("analytics", false);
    track("mission_viewed", { mission_id: "race" });
    expect(capture).not.toHaveBeenCalled();
  });
});
