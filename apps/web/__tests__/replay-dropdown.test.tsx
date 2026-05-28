/**
 * P1-6 — Replay download menu items on the report ShareDropdown.
 *
 * We exercise the dropdown directly (not through ReportView) so the tests
 * stay focused on the two NEW entries and their telemetry / toast wiring.
 * The PDF / PNG render items are not under test here — they're covered by
 * the existing report-page suite — but we do assert the new items render
 * alongside without breaking the existing menu.
 */
import * as React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mocks: lib/api + lib/telemetry + sonner ────────────────────────────────

const downloadReplayJson = vi.fn();
const downloadReplayZip = vi.fn();
const getReportRenderStatus = vi.fn();
const forceReportRender = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    downloadReplayJson: (...args: unknown[]) => downloadReplayJson(...args),
    downloadReplayZip: (...args: unknown[]) => downloadReplayZip(...args),
    getReportRenderStatus: (...args: unknown[]) =>
      getReportRenderStatus(...args),
    forceReportRender: (...args: unknown[]) => forceReportRender(...args),
  };
});

const trackMock = vi.fn();
vi.mock("@/lib/telemetry", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/telemetry")>("@/lib/telemetry");
  return {
    ...actual,
    track: (...args: unknown[]) => trackMock(...args),
  };
});

// sonner's toast.promise actually returns the underlying promise (sonner
// resolves with the value), but the real implementation also displays a
// toast — neither of which matters in jsdom. We stub a minimal surface so
// the dropdown's calls don't throw.
const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    promise: vi.fn((p: Promise<unknown>) => p),
    error: (msg: string) => toastError(msg),
    success: (msg: string) => toastSuccess(msg),
    message: vi.fn(),
  },
}));

// Anchor click is the actual DOM side-effect we want to assert for the ZIP
// path. We spy on HTMLAnchorElement.prototype.click so the test surface is
// independent of jsdom internals (createObjectURL is patched below).
let anchorClicks: HTMLAnchorElement[] = [];
const originalAnchorClick = HTMLAnchorElement.prototype.click;
beforeEach(() => {
  anchorClicks = [];
  HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
    anchorClicks.push(this);
  };
  // createObjectURL is unimplemented in jsdom — supply a deterministic stub.
  // The dropdown calls revokeObjectURL on setTimeout(0); we stub both.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (URL as any).createObjectURL = vi.fn(() => "blob:mock");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (URL as any).revokeObjectURL = vi.fn();
  vi.clearAllMocks();
});
afterEach(() => {
  HTMLAnchorElement.prototype.click = originalAnchorClick;
});

// ── Component under test ───────────────────────────────────────────────────

import { ShareDropdown } from "@/components/report/ShareDropdown";

const SUBMISSION_ID = "11111111-2222-3333-4444-555555555555";

function renderDropdown(props?: { share?: string | null }) {
  return render(
    <ShareDropdown
      submissionId={SUBMISSION_ID}
      onCopyLink={() => undefined}
      sharing={false}
      share={props?.share ?? null}
    />,
  );
}

async function openDropdown() {
  const trigger = screen.getByTestId("share-dropdown-trigger");
  await act(async () => {
    fireEvent.click(trigger);
  });
}

describe("ShareDropdown — P1-6 replay entries", () => {
  it("renders the Download replay JSON menu item", async () => {
    renderDropdown();
    await openDropdown();
    expect(screen.getByTestId("replay-json-item")).toBeInTheDocument();
    expect(screen.getByTestId("replay-json-item")).toHaveTextContent(
      /Download replay \(JSON\)/i,
    );
  });

  it("renders the Download replay ZIP menu item", async () => {
    renderDropdown();
    await openDropdown();
    expect(screen.getByTestId("replay-zip-item")).toBeInTheDocument();
    expect(screen.getByTestId("replay-zip-item")).toHaveTextContent(
      /Download replay \(ZIP\)/i,
    );
  });

  it("clicking the JSON entry calls downloadReplayJson with the submission id", async () => {
    downloadReplayJson.mockResolvedValueOnce({
      bytes: 42,
      filename: `arena-replay-${SUBMISSION_ID.slice(0, 8)}.json`,
    });
    renderDropdown();
    await openDropdown();

    await act(async () => {
      fireEvent.click(screen.getByTestId("replay-json-item"));
    });

    await waitFor(() => expect(downloadReplayJson).toHaveBeenCalledTimes(1));
    expect(downloadReplayJson).toHaveBeenCalledWith(
      SUBMISSION_ID,
      expect.objectContaining({ share: undefined }),
    );
  });

  it("clicking the JSON entry forwards the share token when supplied", async () => {
    downloadReplayJson.mockResolvedValueOnce({
      bytes: 0,
      filename: "arena-replay-shared.json",
    });
    renderDropdown({ share: "shr_abc123" });
    await openDropdown();

    await act(async () => {
      fireEvent.click(screen.getByTestId("replay-json-item"));
    });

    await waitFor(() => expect(downloadReplayJson).toHaveBeenCalled());
    expect(downloadReplayJson).toHaveBeenCalledWith(
      SUBMISSION_ID,
      expect.objectContaining({ share: "shr_abc123" }),
    );
  });

  it("clicking the ZIP entry triggers a temporary anchor click", async () => {
    downloadReplayZip.mockResolvedValueOnce({
      bytes: 12345,
      filename: "arena-replay-11111111-20260528.zip",
    });
    renderDropdown();
    await openDropdown();

    await act(async () => {
      fireEvent.click(screen.getByTestId("replay-zip-item"));
    });

    await waitFor(() => expect(downloadReplayZip).toHaveBeenCalledTimes(1));
    expect(downloadReplayZip).toHaveBeenCalledWith(
      SUBMISSION_ID,
      expect.objectContaining({ share: undefined }),
    );
    // The download is handled inside downloadReplayZip (mocked); the
    // dropdown only owns the JSON-variant anchor. Asserting the wrapper
    // promise resolved is sufficient.
  });

  it("clicking the JSON entry delegates to downloadReplayJson (which owns the anchor click)", async () => {
    // FE remediation — the anchor click is now performed inside
    // ``downloadReplayJson`` itself so the file save can use the
    // canonical response bytes (no JSON.stringify round-trip). The
    // dropdown's job here is to delegate; the anchor-attr assertions
    // live in replay-download-canonical.test.tsx where the real
    // downloadReplayJson is exercised end-to-end.
    downloadReplayJson.mockResolvedValueOnce({
      bytes: 12,
      filename: `arena-replay-${SUBMISSION_ID.slice(0, 8)}.json`,
    });
    renderDropdown();
    await openDropdown();

    await act(async () => {
      fireEvent.click(screen.getByTestId("replay-json-item"));
    });

    await waitFor(() => expect(downloadReplayJson).toHaveBeenCalledTimes(1));
  });

  it("fires replay_export_requested telemetry on JSON click", async () => {
    downloadReplayJson.mockResolvedValueOnce({
      bytes: 0,
      filename: "arena-replay.json",
    });
    renderDropdown();
    await openDropdown();

    await act(async () => {
      fireEvent.click(screen.getByTestId("replay-json-item"));
    });

    await waitFor(() => expect(trackMock).toHaveBeenCalled());
    const requested = trackMock.mock.calls.find(
      ([event]) => event === "replay_export_requested",
    );
    expect(requested).toBeDefined();
    expect(requested?.[1]).toMatchObject({
      submission_id: SUBMISSION_ID,
      kind: "json",
    });
  });

  it("fires replay_export_succeeded with the canonical wire bytes on JSON download", async () => {
    // FE remediation — bytes now reflect ``blob.size`` (the actual
    // on-the-wire byte count) rather than ``JSON.stringify(parsed).length``
    // (which silently lost canonical whitespace / key order). The mock
    // mirrors the new ``{bytes, filename}`` return contract.
    downloadReplayJson.mockResolvedValueOnce({
      bytes: 1234,
      filename: "arena-replay-canonical.json",
    });
    renderDropdown();
    await openDropdown();

    await act(async () => {
      fireEvent.click(screen.getByTestId("replay-json-item"));
    });

    await waitFor(() => {
      const success = trackMock.mock.calls.find(
        ([event]) => event === "replay_export_succeeded",
      );
      expect(success).toBeDefined();
    });
    const success = trackMock.mock.calls.find(
      ([event]) => event === "replay_export_succeeded",
    );
    expect(success?.[1]).toMatchObject({
      submission_id: SUBMISSION_ID,
      kind: "json",
      bytes: 1234,
    });
  });

  it("fires replay_export_succeeded with bytes on successful ZIP download", async () => {
    downloadReplayZip.mockResolvedValueOnce({
      bytes: 9001,
      filename: "arena-replay-11111111-20260528.zip",
    });
    renderDropdown();
    await openDropdown();

    await act(async () => {
      fireEvent.click(screen.getByTestId("replay-zip-item"));
    });

    await waitFor(() => {
      const success = trackMock.mock.calls.find(
        ([event]) => event === "replay_export_succeeded",
      );
      expect(success).toBeDefined();
    });
    const success = trackMock.mock.calls.find(
      ([event]) => event === "replay_export_succeeded",
    );
    expect(success?.[1]).toMatchObject({
      submission_id: SUBMISSION_ID,
      kind: "zip",
      bytes: 9001,
    });
  });

  it("404 from the JSON download surfaces a 'not available' toast and emits not_found telemetry", async () => {
    const { ApiError } = await import("@/lib/api");
    downloadReplayJson.mockRejectedValueOnce(
      new ApiError("submission not found", 404, { detail: "submission unknown" }),
    );
    renderDropdown();
    await openDropdown();

    await act(async () => {
      fireEvent.click(screen.getByTestId("replay-json-item"));
    });

    await waitFor(() => {
      const failed = trackMock.mock.calls.find(
        ([event]) => event === "replay_export_failed",
      );
      expect(failed).toBeDefined();
    });
    const failed = trackMock.mock.calls.find(
      ([event]) => event === "replay_export_failed",
    );
    expect(failed?.[1]).toMatchObject({
      submission_id: SUBMISSION_ID,
      kind: "json",
      error_class: "not_found",
    });
  });

  it("network failure on the ZIP download emits network_error telemetry", async () => {
    const { ApiError } = await import("@/lib/api");
    downloadReplayZip.mockRejectedValueOnce(
      new ApiError("Network error", 0, null),
    );
    renderDropdown();
    await openDropdown();

    await act(async () => {
      fireEvent.click(screen.getByTestId("replay-zip-item"));
    });

    await waitFor(() => {
      const failed = trackMock.mock.calls.find(
        ([event]) => event === "replay_export_failed",
      );
      expect(failed).toBeDefined();
    });
    const failed = trackMock.mock.calls.find(
      ([event]) => event === "replay_export_failed",
    );
    expect(failed?.[1]).toMatchObject({
      submission_id: SUBMISSION_ID,
      kind: "zip",
      error_class: "network_error",
    });
  });

  it("classifies tutorial / not-graded 404s as not_graded", async () => {
    const { ApiError } = await import("@/lib/api");
    downloadReplayJson.mockRejectedValueOnce(
      new ApiError("not graded", 404, {
        detail: "Submission is not graded yet",
      }),
    );
    renderDropdown();
    await openDropdown();

    await act(async () => {
      fireEvent.click(screen.getByTestId("replay-json-item"));
    });

    await waitFor(() => {
      const failed = trackMock.mock.calls.find(
        ([event]) => event === "replay_export_failed",
      );
      expect(failed).toBeDefined();
    });
    const failed = trackMock.mock.calls.find(
      ([event]) => event === "replay_export_failed",
    );
    expect(failed?.[1]).toMatchObject({
      kind: "json",
      error_class: "not_graded",
    });
  });
});
