import * as React from "react";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Next/link as a plain anchor — matches the convention in landing.test.tsx.
vi.mock("next/link", () => ({
  __esModule: true,
  default: ({
    children,
    href,
    ...rest
  }: { children: React.ReactNode; href: string } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

// Mock the API client so we can assert which kinds got POSTed without
// touching the real network. ``vi.hoisted`` is required because ``vi.mock``
// is hoisted to the top of the file; without it, the factory would
// reference ``setConsentRecord`` before the const initialisation runs.
const { setConsentRecord } = vi.hoisted(() => ({
  setConsentRecord: vi.fn(async () => undefined),
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    auth: { ...actual.auth, setConsentRecord },
  };
});

// Import AFTER the mocks so the component picks up the stubs.
import { CookieConsentBanner } from "@/components/legal/CookieConsentBanner";
import {
  LATEST_CONSENT_VERSION,
  STORAGE_KEY,
  STORAGE_VERSION_KEY,
  getConsent,
} from "@/lib/consent";

beforeEach(() => {
  window.localStorage.clear();
  setConsentRecord.mockClear();
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("CookieConsentBanner", () => {
  it("renders on a first visit (no stored consent)", async () => {
    render(<CookieConsentBanner />);
    await waitFor(() =>
      expect(
        screen.getByRole("region", { name: /cookie consent/i }),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: /accept all cookies/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /accept essential cookies only/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /customize cookie preferences/i }),
    ).toBeInTheDocument();
  });

  it("does NOT render when consent has been recorded at the current version", async () => {
    const now = new Date().toISOString();
    const state = {
      analytics: { granted: true, version: LATEST_CONSENT_VERSION, at: now },
      functional: { granted: true, version: LATEST_CONSENT_VERSION, at: now },
      marketing: { granted: false, version: LATEST_CONSENT_VERSION, at: now },
    };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    window.localStorage.setItem(
      STORAGE_VERSION_KEY,
      String(LATEST_CONSENT_VERSION),
    );

    render(<CookieConsentBanner />);
    // Banner is gated by an effect; wait a tick before asserting absence.
    await waitFor(() => {
      expect(
        screen.queryByRole("region", { name: /cookie consent/i }),
      ).not.toBeInTheDocument();
    });
  });

  it("re-renders the banner when the stored version is older than current", async () => {
    const now = new Date().toISOString();
    const stale = LATEST_CONSENT_VERSION - 1;
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        analytics: { granted: true, version: stale, at: now },
        functional: { granted: true, version: stale, at: now },
        marketing: { granted: false, version: stale, at: now },
      }),
    );
    window.localStorage.setItem(STORAGE_VERSION_KEY, String(stale));

    render(<CookieConsentBanner />);
    await waitFor(() =>
      expect(
        screen.getByRole("region", { name: /cookie consent/i }),
      ).toBeInTheDocument(),
    );
  });

  it("Accept all closes the banner, persists three true records, and POSTs each kind", async () => {
    render(<CookieConsentBanner />);
    const acceptAll = await screen.findByRole("button", {
      name: /accept all cookies/i,
    });

    fireEvent.click(acceptAll);

    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: /cookie consent/i }),
      ).not.toBeInTheDocument(),
    );

    const persisted = getConsent();
    expect(persisted.analytics?.granted).toBe(true);
    expect(persisted.functional?.granted).toBe(true);
    expect(persisted.marketing?.granted).toBe(true);
    expect(persisted.analytics?.version).toBe(LATEST_CONSENT_VERSION);

    await waitFor(() =>
      expect(setConsentRecord).toHaveBeenCalledTimes(3),
    );
    const kinds = setConsentRecord.mock.calls.map((call) => {
      const [first] = call as unknown as [{ kind: string; granted: boolean }];
      return first.kind;
    });
    expect(new Set(kinds)).toEqual(new Set(["analytics", "functional", "marketing"]));
    expect(
      setConsentRecord.mock.calls.every(
        (call) => (call as unknown as [{ granted: boolean }])[0].granted === true,
      ),
    ).toBe(true);
  });

  it("Essential only persists analytics=false, functional=true, marketing=false", async () => {
    render(<CookieConsentBanner />);
    const essentialOnly = await screen.findByRole("button", {
      name: /accept essential cookies only/i,
    });

    fireEvent.click(essentialOnly);

    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: /cookie consent/i }),
      ).not.toBeInTheDocument(),
    );

    const persisted = getConsent();
    expect(persisted.analytics?.granted).toBe(false);
    expect(persisted.functional?.granted).toBe(true);
    expect(persisted.marketing?.granted).toBe(false);
  });
});
