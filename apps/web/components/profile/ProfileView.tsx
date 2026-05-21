"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, UserX } from "lucide-react";
import { ApiError, getProfile } from "@/lib/api";
import { ProfileHeader } from "./ProfileHeader";
import { BadgeGrid } from "./BadgeGrid";
import { MissionHistoryTable } from "./MissionHistoryTable";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { track } from "@/lib/telemetry";

interface ProfileViewProps {
  handle: string;
}

/**
 * Client view for `/profile/[handle]`. Open to anyone, even signed-out users.
 * 404 from the backend renders an empathic "no such profile" state, not a
 * generic error toast.
 */
export function ProfileView({ handle }: ProfileViewProps) {
  const profileQuery = useQuery({
    queryKey: ["profile", handle],
    queryFn: ({ signal }) => getProfile(handle, signal),
    retry: (failureCount, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return failureCount < 1;
    },
  });

  // Fire `profile_viewed` once per handle on first successful load. We pass
  // only the handle — never the user's email or any PII from the response.
  const trackedHandleRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (profileQuery.data && trackedHandleRef.current !== handle) {
      trackedHandleRef.current = handle;
      track("profile_viewed", { handle });
    }
  }, [profileQuery.data, handle]);

  if (profileQuery.isLoading) {
    return (
      <main className="mx-auto max-w-5xl space-y-8 px-6 py-12">
        <Skeleton className="h-24 w-full rounded-xl" />
        <Skeleton className="h-40 w-full rounded-xl" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </main>
    );
  }

  if (profileQuery.error) {
    const err =
      profileQuery.error instanceof ApiError ? profileQuery.error : null;

    if (err?.status === 404) {
      return (
        <div className="mx-auto max-w-2xl px-6 py-16 text-center">
          <UserX
            className="mx-auto size-6 text-[var(--color-muted-foreground)]"
            aria-hidden
          />
          <h1 className="mt-3 text-2xl font-semibold tracking-tight">
            Profile not found
          </h1>
          <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
            No one with the handle{" "}
            <code className="font-mono">@{handle}</code> has signed up yet.
          </p>
          <Button asChild variant="secondary" className="mt-6">
            <Link href="/missions">Browse missions</Link>
          </Button>
        </div>
      );
    }

    return (
      <div className="mx-auto max-w-2xl px-6 py-16 text-center">
        <AlertCircle
          className="mx-auto size-6 text-[var(--color-danger)]"
          aria-hidden
        />
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">
          Profile unavailable
        </h1>
        <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
          {err?.status === 0
            ? "Couldn't reach the API. Is the backend running?"
            : (err?.message ?? "Unexpected error.")}
        </p>
        <Button
          variant="secondary"
          className="mt-6"
          onClick={() => void profileQuery.refetch()}
        >
          Try again
        </Button>
      </div>
    );
  }

  const profile = profileQuery.data;
  if (!profile) return null;

  const earnedIds = new Set(profile.badges.map((b) => b.id));

  return (
    <main className="mx-auto max-w-5xl space-y-10 px-6 py-12">
      <section
        className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-6 shadow-soft"
        aria-labelledby="profile-header"
      >
        <ProfileHeader profile={profile} />
      </section>

      <section aria-labelledby="badges-heading">
        <h2
          id="badges-heading"
          className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]"
        >
          Badges ({profile.badges.length})
        </h2>
        <BadgeGrid badges={profile.badges} earnedIds={earnedIds} />
      </section>

      <section aria-labelledby="history-heading">
        <h2
          id="history-heading"
          className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]"
        >
          Mission history
        </h2>
        <MissionHistoryTable items={profile.history} />
      </section>
    </main>
  );
}
