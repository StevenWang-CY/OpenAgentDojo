"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
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
  detail: string;
  code: "active_session_exists";
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
  const mutation = useMutation({
    mutationFn: () => createSession({ mission_id: missionId }),
    onSuccess(session) {
      track("mission_started", { mission_id: missionId, session_id: session.id });
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
    <Button
      size="lg"
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
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
  );
}
