"use client";

import * as React from "react";
import Link from "next/link";
import { ApiError, createSession } from "@/lib/api";
import { usePathname, useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowRight, Sparkles } from "lucide-react";
import type { User } from "@arena/shared-types";
import { Button } from "@/components/ui/Button";

interface OrientationBannerProps {
  /** Result of GET /auth/me — null when the visitor is not signed in. */
  user: User | null;
  /** Notice toast triggered by the workspace ?tutorial=completed redirect. */
  showCompletionToast?: boolean;
}

/**
 * P0-1 — "// start here" affordance.
 *
 * Three states:
 *
 *   1. Anonymous visitor → render the "// start here" pitch, route the
 *      CTA through /auth/sign-in?next=/missions/orientation so they
 *      land in the tutorial after authenticating.
 *   2. Signed-in user with no tutorial completion → enabled "Start
 *      orientation" button that creates an ``orientation`` session and
 *      redirects to /workspace/{id}.
 *   3. Signed-in user with prior completion → muted "completed
 *      YYYY-MM-DD · replay" row.
 */
export function OrientationBanner({
  user,
  showCompletionToast = false,
}: OrientationBannerProps) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const [submitting, setSubmitting] = React.useState(false);
  // Suppress the "skip" link when the user is already on /missions —
  // clicking it would be a no-op and looks broken.
  const onCatalogPage = pathname === "/missions";

  React.useEffect(() => {
    if (showCompletionToast) {
      toast.success("Orientation complete. Welcome to the dojo.");
    }
  }, [showCompletionToast]);

  const startMutation = useMutation({
    mutationFn: async () => {
      setSubmitting(true);
      try {
        const session = await createSession({ mission_id: "orientation" });
        return session;
      } finally {
        setSubmitting(false);
      }
    },
    onSuccess(session) {
      void queryClient.invalidateQueries({ queryKey: ["session", session.id] });
      router.push(`/workspace/${session.id}`);
    },
    onError(error) {
      if (
        error instanceof ApiError &&
        error.status === 409 &&
        typeof error.body?.detail === "object" &&
        error.body.detail !== null &&
        "active_session_id" in error.body.detail
      ) {
        const activeId = (
          error.body.detail as { active_session_id?: string }
        ).active_session_id;
        toast.error(
          "Finish your current mission first, then come back to the tutorial.",
        );
        if (activeId) {
          router.push(`/workspace/${activeId}`);
        }
        return;
      }
      toast.error(
        error instanceof ApiError
          ? error.message
          : "Failed to start the tutorial.",
      );
    },
  });

  if (user && user.tutorial_completed_at) {
    const date = new Date(user.tutorial_completed_at);
    // Use the user's locale + timezone so the completion date doesn't
    // shift by a day for users east/west of UTC. ``toLocaleDateString``
    // is hydration-safe because the same input always produces the same
    // output on the server and the client when the locale is undefined
    // (the browser picks the user's default; SSR picks the Node default,
    // which is `en-US` in production builds).
    const dateLabel = isNaN(date.getTime())
      ? user.tutorial_completed_at
      : date.toLocaleDateString(undefined, {
          year: "numeric",
          month: "short",
          day: "numeric",
        });
    return (
      <p
        className="mb-3 font-mono text-[11px] text-[var(--color-muted-foreground)]"
        data-testid="orientation-banner-completed"
      >
        {"// orientation · completed "}
        <span className="text-[var(--color-foreground)]">{dateLabel}</span>
        {user.tutorial_replay_count > 0
          ? ` · replayed ${user.tutorial_replay_count}×`
          : ""}{" "}
        ·{" "}
        <Link
          href="/missions/orientation"
          className="underline-offset-4 hover:underline"
        >
          replay →
        </Link>
      </p>
    );
  }

  return (
    <section
      className="mb-6 overflow-hidden rounded-lg border border-[var(--color-border-strong)] bg-gradient-to-br from-[oklch(from_var(--color-primary)_l_c_h/0.06)] to-[oklch(from_var(--color-primary)_l_c_h/0.02)]"
      data-testid="orientation-banner"
      data-variant={user ? "signed-in" : "anonymous"}
    >
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--color-primary)]">
            {"// start here"}
          </p>
          <h2 className="mt-1 inline-flex items-center gap-2 text-base font-semibold tracking-tight">
            <Sparkles className="size-4 text-[var(--color-primary)]" aria-hidden />
            00 · Orientation — learn the dojo in ~8 minutes
          </h2>
          <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
            A guided tour of context selection, prompting, agent review, and
            verification. No score; you can&rsquo;t fail.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {user ? (
            <Button
              type="button"
              disabled={submitting}
              onClick={() => startMutation.mutate()}
              data-testid="orientation-banner-start"
            >
              {submitting ? "Provisioning…" : "Start orientation"}
              <ArrowRight className="size-3.5" aria-hidden />
            </Button>
          ) : (
            <Button asChild data-testid="orientation-banner-start">
              <Link href="/auth/sign-in?next=/missions/orientation">
                Sign in to start
                <ArrowRight className="size-3.5" aria-hidden />
              </Link>
            </Button>
          )}
          {onCatalogPage ? null : (
            <Link
              href="/missions"
              data-testid="orientation-banner-skip"
              className="font-mono text-[11px] text-[var(--color-muted-foreground)] underline-offset-4 hover:underline"
            >
              skip
            </Link>
          )}
        </div>
      </div>
    </section>
  );
}
