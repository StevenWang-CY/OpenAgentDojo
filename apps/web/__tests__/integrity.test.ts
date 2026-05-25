/**
 * P0-8 — IntegritySignaller emits tab.blurred / paste.large on the
 * documented browser events and respects the per-kind debounce.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  IntegritySignaller,
  PASTE_LARGE_THRESHOLD_CHARS,
  inferPasteTarget,
} from "@/lib/integrity";

const SESSION_ID = "00000000-0000-0000-0000-0000000000aa";

function mkPost() {
  const post = vi.fn(async () => undefined);
  return post;
}

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = "";
});

describe("IntegritySignaller", () => {
  it("attaches no listeners in honor mode", () => {
    const post = mkPost();
    const sig = new IntegritySignaller({
      sessionId: SESSION_ID,
      mode: "self_study",
      post,
    }).start();

    window.dispatchEvent(new Event("blur"));
    expect(post).not.toHaveBeenCalled();
    sig.dispose();
  });

  it("emits tab.blurred when proctored window loses focus", async () => {
    const post = mkPost();
    const sig = new IntegritySignaller({
      sessionId: SESSION_ID,
      mode: "proctored",
      post,
    }).start();

    window.dispatchEvent(new Event("blur"));
    // Drain the microtask the emit schedules.
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(post).toHaveBeenCalledWith(
      SESSION_ID,
      "tab.blurred",
      expect.objectContaining({ seconds_visible_before: expect.any(Number) }),
    );
    sig.dispose();
  });

  it("emits paste.large when the pasted text exceeds 200 chars", async () => {
    const post = mkPost();
    const target = document.createElement("div");
    target.dataset.pasteTarget = "agent_chat";
    document.body.append(target);

    const sig = new IntegritySignaller({
      sessionId: SESSION_ID,
      mode: "proctored",
      post,
    }).start();

    const longText = "x".repeat(PASTE_LARGE_THRESHOLD_CHARS + 50);
    const ev = new Event("paste", { bubbles: true }) as ClipboardEvent;
    // jsdom doesn't construct a real ClipboardEvent with clipboardData;
    // patch the property so the signaller can read the payload size.
    Object.defineProperty(ev, "clipboardData", {
      value: { getData: () => longText },
    });
    Object.defineProperty(ev, "target", { value: target });
    document.dispatchEvent(ev);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(post).toHaveBeenCalledWith(
      SESSION_ID,
      "paste.large",
      expect.objectContaining({
        chars: longText.length,
        target: "agent_chat",
      }),
    );
    sig.dispose();
  });

  it("does not emit paste.large for short pastes", async () => {
    const post = mkPost();
    const sig = new IntegritySignaller({
      sessionId: SESSION_ID,
      mode: "proctored",
      post,
    }).start();

    const ev = new Event("paste") as ClipboardEvent;
    Object.defineProperty(ev, "clipboardData", {
      value: { getData: () => "short" },
    });
    document.dispatchEvent(ev);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(post).not.toHaveBeenCalled();
    sig.dispose();
  });

  it("debounces rapid emissions of the same kind", async () => {
    const post = mkPost();
    const sig = new IntegritySignaller({
      sessionId: SESSION_ID,
      mode: "proctored",
      post,
    }).start();

    // Fire two blurs in immediate succession.
    await sig.emit("tab.blurred", { seconds_visible_before: 0 });
    await sig.emit("tab.blurred", { seconds_visible_before: 0 });

    expect(post).toHaveBeenCalledTimes(1);
    sig.dispose();
  });

  it("dispose() removes all listeners", () => {
    const post = mkPost();
    const sig = new IntegritySignaller({
      sessionId: SESSION_ID,
      mode: "proctored",
      post,
    }).start();
    sig.dispose();
    window.dispatchEvent(new Event("blur"));
    expect(post).not.toHaveBeenCalled();
  });
});

describe("inferPasteTarget", () => {
  it("returns 'other' when no ancestor declares data-paste-target", () => {
    const el = document.createElement("div");
    document.body.append(el);
    expect(inferPasteTarget(el)).toBe("other");
  });

  it("returns the documented value from the nearest ancestor", () => {
    const outer = document.createElement("section");
    outer.dataset.pasteTarget = "terminal";
    const inner = document.createElement("textarea");
    outer.append(inner);
    document.body.append(outer);
    expect(inferPasteTarget(inner)).toBe("terminal");
  });

  it("falls back to 'other' for unknown target values", () => {
    const outer = document.createElement("section");
    outer.dataset.pasteTarget = "screen_share"; // not in the union
    const inner = document.createElement("textarea");
    outer.append(inner);
    document.body.append(outer);
    expect(inferPasteTarget(inner)).toBe("other");
  });
});
