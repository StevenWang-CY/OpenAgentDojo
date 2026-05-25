/**
 * B.5 — IntegritySignaller scopes the contextmenu handler to
 * ``[data-paste-target]`` zones.
 *
 *   * Right-click INSIDE a paste-target ancestor (editor, agent chat,
 *     terminal) → ``preventDefault()`` is called AND a
 *     ``proctored.violation`` event is emitted.
 *   * Right-click OUTSIDE any paste-target zone → no preventDefault, no
 *     emission. The surrounding application chrome retains its native
 *     context menus (accessibility-critical for links, form inputs).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { IntegritySignaller } from "@/lib/integrity";

const SESSION_ID = "00000000-0000-0000-0000-000000000bb1";

afterEach(() => {
  document.body.innerHTML = "";
});

describe("IntegritySignaller contextmenu scoping (B.5)", () => {
  it("emits + preventDefaults right-clicks inside a paste-target ancestor", async () => {
    const post = vi.fn(async () => undefined);
    const zone = document.createElement("section");
    zone.dataset.pasteTarget = "editor";
    const inner = document.createElement("div");
    zone.append(inner);
    document.body.append(zone);

    const sig = new IntegritySignaller({
      sessionId: SESSION_ID,
      mode: "proctored",
      post,
    }).start();

    const ev = new Event("contextmenu", {
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(ev, "target", { value: inner });
    const preventSpy = vi.spyOn(ev, "preventDefault");
    document.dispatchEvent(ev);
    // The emit posts asynchronously — drain the microtask queue.
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(preventSpy).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      SESSION_ID,
      "proctored.violation",
      expect.objectContaining({
        kind: "context_menu",
        target: "editor",
      }),
    );
    sig.dispose();
  });

  it("ignores right-clicks outside any paste-target ancestor", async () => {
    const post = vi.fn(async () => undefined);
    const outside = document.createElement("nav");
    document.body.append(outside);

    const sig = new IntegritySignaller({
      sessionId: SESSION_ID,
      mode: "proctored",
      post,
    }).start();

    const ev = new Event("contextmenu", {
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(ev, "target", { value: outside });
    const preventSpy = vi.spyOn(ev, "preventDefault");
    document.dispatchEvent(ev);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(preventSpy).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
    sig.dispose();
  });

  it("ignores right-clicks when the event target is not an Element", async () => {
    const post = vi.fn(async () => undefined);
    const sig = new IntegritySignaller({
      sessionId: SESSION_ID,
      mode: "proctored",
      post,
    }).start();

    const ev = new Event("contextmenu", {
      bubbles: true,
      cancelable: true,
    });
    // jsdom default event target is null for synthetic events dispatched
    // on ``document``; the guard MUST short-circuit cleanly.
    const preventSpy = vi.spyOn(ev, "preventDefault");
    document.dispatchEvent(ev);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(preventSpy).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
    sig.dispose();
  });
});
