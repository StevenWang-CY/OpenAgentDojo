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

export function ProfileView({ handle }: ProfileViewProps) {
  const profileQuery = useQuery({
    queryKey: ["profile", handle],
    queryFn: ({ signal }) => getProfile(handle, signal),
    retry: (failureCount, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return failureCount < 1;
    },
  });

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
        <Skeleton className="h-28 w-full rounded-lg" />
        <Skeleton className="h-40 w-full rounded-lg" />
        <Skeleton className="h-64 w-full rounded-lg" />
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

  return (
    <main
      className="mx-auto max-w-5xl px-6 pt-12 pb-16"
      aria-labelledby="profile-header"
    >
      <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
        <span className="text-[var(--color-primary)]">{"//"}</span> public
        profile
      </p>

      <section className="mt-3" aria-labelledby="profile-header">
        <ProfileHeader profile={profile} />
      </section>

      <SectionHeading
        title="badges earned"
        count={`${profile.badges.length} earned`}
        id="badges-heading"
      />
      <BadgeGrid badges={profile.badges} />

      <SectionHeading
        title="mission history"
        count={`${profile.history.length} session${profile.history.length === 1 ? "" : "s"}`}
        id="history-heading"
      />
      <MissionHistoryTable items={profile.history} />
    </main>
  );
}

function SectionHeading({
  title,
  count,
  id,
}: {
  title: string;
  count?: string;
  id?: string;
}) {
  return (
    <div className="mt-12 flex items-baseline justify-between border-b border-[var(--color-border)] pb-2.5">
      <h2
        id={id}
        className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]"
      >
        {"// "}
        {title}
      </h2>
      {count ? (
        <p className="font-mono text-[11px] text-[var(--color-muted-foreground)]">
          {count}
        </p>
      ) : null}
    </div>
  );
}
