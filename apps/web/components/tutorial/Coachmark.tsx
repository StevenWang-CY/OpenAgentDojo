"use client";

import * as React from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import type { TutorialStep } from "./tutorial-steps";

interface CoachmarkProps {
  step: TutorialStep;
  totalSteps: number;
  onDismiss: () => void;
  onContinue: () => void;
}

/**
 * Single popover anchored to a DOM element marked
 * ``data-tutorial-anchor={anchor}``. Falls back to the bottom-center of
 * the viewport when the anchor isn't mounted yet (Coachmark renders
 * during the brief gap between session-loaded and workspace-shell-paint).
 *
 * Designed as a portal so the coachmark sits above any z-index inside
 * the workspace panes (Monaco editor's overlay layer, the resizable
 * splitter handles, etc.). The portal target is ``document.body`` —
 * server-render gates the entire component behind a mount flag so we
 * never call ``document.*`` on the server.
 */
export function Coachmark({
  step,
  totalSteps,
  onDismiss,
  onContinue,
}: CoachmarkProps) {
  const [mounted, setMounted] = React.useState(false);
  const [position, setPosition] = React.useState<{
    top: number;
    left: number;
    placement: "above" | "below" | "center";
  } | null>(null);

  React.useEffect(() => {
    setMounted(true);
  }, []);

  // Re-position whenever the step changes OR the window resizes. We
  // tail every animation frame for ~600ms after step change so the
  // anchor's settle motion (Monaco initialisation, panel resize) gets
  // tracked instead of pinning to the first frame's rect.
  React.useEffect(() => {
    if (!mounted) return;
    let cancelled = false;

    function place() {
      if (cancelled) return;
      const anchorEl = document.querySelector<HTMLElement>(
        `[data-tutorial-anchor='${step.anchor}']`,
      );
      if (!anchorEl) {
        // Anchor not mounted yet — fall back to bottom-center.
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        setPosition({
          top: Math.max(16, vh - 220),
          left: Math.max(16, Math.floor(vw / 2) - 200),
          placement: "center",
        });
        return;
      }
      const rect = anchorEl.getBoundingClientRect();
      const popoverWidth = 360;
      const popoverHeight = 180;
      const margin = 12;
      // Prefer placing the popover below the anchor; if that overflows
      // the viewport vertically, swap to above.
      const placeBelow =
        rect.bottom + popoverHeight + margin < window.innerHeight;
      const top = placeBelow
        ? rect.bottom + margin
        : Math.max(margin, rect.top - popoverHeight - margin);
      const left = Math.min(
        Math.max(margin, rect.left + rect.width / 2 - popoverWidth / 2),
        window.innerWidth - popoverWidth - margin,
      );
      setPosition({ top, left, placement: placeBelow ? "below" : "above" });
    }

    place();
    const handle = () => place();
    window.addEventListener("resize", handle);
    window.addEventListener("scroll", handle, true);
    const ticks: number[] = [];
    for (let i = 1; i <= 6; i += 1) {
      ticks.push(window.setTimeout(handle, i * 100));
    }
    return () => {
      cancelled = true;
      window.removeEventListener("resize", handle);
      window.removeEventListener("scroll", handle, true);
      ticks.forEach((t) => window.clearTimeout(t));
    };
  }, [mounted, step.anchor, step.id]);

  if (!mounted || !position) return null;

  return createPortal(
    <div
      aria-live="polite"
      aria-atomic
      role="dialog"
      aria-label={`Tutorial step ${step.index} of ${totalSteps}: ${step.title}`}
      className={
        "fixed z-[100] w-[360px] max-w-[calc(100vw-32px)] rounded-xl border " +
        "border-[var(--color-border-strong)] bg-[var(--color-surface)] " +
        "p-4 shadow-2xl transition-opacity duration-200 ease-macos " +
        "ring-1 ring-black/5 backdrop-blur"
      }
      style={{ top: position.top, left: position.left }}
      data-testid="tutorial-coachmark"
      data-step-id={step.id}
    >
      <div className="mb-2 flex items-center justify-between font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
        <span>
          {"// "}step {step.index} / {totalSteps}
        </span>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss this coachmark"
          className="rounded-md p-1 text-[var(--color-muted-foreground)] transition-colors duration-150 ease-macos hover:bg-[var(--color-muted)] hover:text-[var(--color-foreground)]"
        >
          <X className="size-3.5" aria-hidden />
        </button>
      </div>
      <h2 className="mb-1.5 text-sm font-semibold leading-snug">
        {step.title}
      </h2>
      <p className="text-[13px] leading-relaxed text-[var(--color-muted-foreground)]">
        {step.body}
      </p>
      <div className="mt-3.5 flex items-center justify-between gap-2">
        <div
          aria-hidden
          className="flex items-center gap-1.5"
          data-testid="tutorial-step-dots"
        >
          {Array.from({ length: totalSteps }).map((_, i) => (
            <span
              key={i}
              className={
                "h-1.5 w-1.5 rounded-full transition-colors duration-150 " +
                (i + 1 <= step.index
                  ? "bg-[var(--color-primary)]"
                  : "bg-[var(--color-border-strong)]")
              }
            />
          ))}
        </div>
        <Button
          size="sm"
          type="button"
          onClick={onContinue}
          data-testid="tutorial-step-continue"
        >
          Got it
        </Button>
      </div>
    </div>,
    document.body,
  );
}
