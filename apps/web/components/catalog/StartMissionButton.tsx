"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { Info, Loader2 } from "lucide-react";
import { toast } from "sonner";
import type { SessionMode } from "@arena/shared-types";
import { ApiError, createSession } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { track } from "@/lib/telemetry";

interface StartMissionButtonProps {
  missionId: string;
}

/**
 * Shape of the 409 ``detail`` body returned by ``POST /sessions`` when the
 * per-user concurrency cap is hit (M8 §21). FastAPI nests our dict under the
 * top-level ``detail`` key, so ``ApiError.body.detail`` is this object.
 */
interface ActiveSessionConflict {
  code: "active_session_exists";
  message: string;
  active_session_id: string;
}

function readActiveSessionConflict(
  error: ApiError
): ActiveSessionConflict | null {
  if (error.status !== 409) return null;
  const inner = error.body?.detail;
  if (
    inner &&
    typeof inner === "object" &&
    !Array.isArray(inner) &&
    "active_session_id" in inner &&
    typeof (inner as { active_session_id?: unknown }).active_session_id ===
      "string"
  ) {
    return inner as unknown as ActiveSessionConflict;
  }
  return null;
}

export function StartMissionButton({ missionId }: StartMissionButtonProps) {
  const router = useRouter();
  // P0-8 — anti-cheating posture toggle. Default is ``self_study`` (honor
  // mode); the user opts into ``proctored`` per attempt at session create
  // time. The choice is sticky for the lifetime of this component
  // instance only — navigating away and back resets to ``self_study`` so
  // a forgotten checkbox can never silently stamp a verified credential.
  const [mode, setMode] = React.useState<SessionMode>("self_study");
  const [helpOpen, setHelpOpen] = React.useState(false);
  const mutation = useMutation({
    mutationFn: () => createSession({ mission_id: missionId, mode }),
    onSuccess(session) {
      track("mission_started", {
        mission_id: missionId,
        session_id: session.id,
        mode,
      });
      // No toast here — the workspace renders a full-page "Provisioning your
      // sandbox…" message immediately, which is more informative than a
      // floating toast and avoids a Sonner-portal race with router.push that
      // intermittently triggers "insertBefore on Node" on Next.js 15 / React 19.
      router.push(`/workspace/${session.id}`);
    },
    onError(error) {
      if (error instanceof ApiError && error.status === 401) {
        toast.error("Please sign in to start a mission.");
        router.push("/auth/sign-in");
        return;
      }
      if (error instanceof ApiError) {
        const conflict = readActiveSessionConflict(error);
        if (conflict) {
          const resumeHref = `/workspace/${conflict.active_session_id}`;
          toast.message("You already have an active session", {
            description: "Finish it (or abandon it) before starting another.",
            action: {
              label: "Resume",
              onClick: () => router.push(resumeHref),
            },
          });
          return;
        }
      }
      const msg =
        error instanceof ApiError
          ? error.status === 0
            ? "Couldn't reach the API. Is the backend running?"
            : error.message
          : "Failed to start the mission.";
      toast.error(msg);
    },
  });

  return (
    <div className="flex flex-col gap-3">
      <fieldset
        className="flex flex-col gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
        aria-label="Anti-cheating posture"
      >
        <div className="flex items-baseline justify-between">
          <legend className="font-mono text-[10.5px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
            {"// posture"}
          </legend>
          <button
            type="button"
            aria-expanded={helpOpen}
            aria-controls="posture-popover"
            onClick={() => setHelpOpen((p) => !p)}
            className="inline-flex items-center gap-1 text-[10.5px] text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
            data-testid="posture-help-toggle"
          >
            <Info className="size-3" aria-hidden />
            what is this?
          </button>
        </div>
        <label className="flex cursor-pointer items-start gap-2 text-[12px]">
          <input
            type="radio"
            name="session-mode"
            value="self_study"
            checked={mode === "self_study"}
            onChange={() => setMode("self_study")}
            className="mt-1 size-3.5 accent-[var(--color-primary)]"
            data-testid="posture-radio-self-study"
          />
          <span className="flex flex-col">
            <span className="font-medium">Honor mode</span>
            <span className="text-[10.5px] text-[var(--color-muted-foreground)]">
              Practice — your score will NOT be a verified credential.
            </span>
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-2 text-[12px]">
          <input
            type="radio"
            name="session-mode"
            value="proctored"
            checked={mode === "proctored"}
            onChange={() => setMode("proctored")}
            className="mt-1 size-3.5 accent-[var(--color-primary)]"
            data-testid="posture-radio-proctored"
          />
          <span className="flex flex-col">
            <span className="font-medium">Proctored</span>
            <span className="text-[10.5px] text-[var(--color-muted-foreground)]">
              Verified credential — emits tab/focus/paste integrity signals.
            </span>
          </span>
        </label>
        {helpOpen ? (
          <div
            id="posture-popover"
            role="note"
            className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-2 text-[10.5px] text-[var(--color-muted-foreground)]"
          >
            Honor mode is for learning — your work is recorded, but the
            score isn&apos;t a credential. Proctored mode opts into browser
            integrity signals (tab blur, large paste, context menu) so
            the resulting score can be verified by a third party.{" "}
            <Link
              href="/help/honor-mode"
              className="font-mono text-[var(--color-primary)] underline-offset-2 hover:underline"
            >
              policy
            </Link>
          </div>
        ) : null}
      </fieldset>
      <Button
        size="lg"
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
        data-testid="start-mission-button"
      >
        {mutation.isPending ? (
          <Loader2 className="size-4 animate-spin" aria-hidden />
        ) : (
          <span aria-hidden className="font-mono text-[13px] leading-none">
            ▶
          </span>
        )}
        {mutation.isPending ? "Starting…" : "Start mission"}
        {!mutation.isPending ? (
          <span aria-hidden className="ml-1 opacity-70">
            →
          </span>
        ) : null}
      </Button>
    </div>
  );
}
