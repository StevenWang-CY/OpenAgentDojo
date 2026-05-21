import type { PublicProfile } from "@arena/shared-types";
import { formatDate } from "@/lib/format";

interface ProfileHeaderProps {
  profile: PublicProfile;
}

export function ProfileHeader({ profile }: ProfileHeaderProps) {
  const name = profile.display_name ?? profile.handle;
  return (
    <header className="flex flex-col items-start gap-4 sm:flex-row sm:items-center">
      <div
        aria-hidden
        className="grid size-14 place-items-center rounded-xl bg-[var(--color-primary)] text-lg font-semibold uppercase text-[var(--color-primary-foreground)] shadow-soft"
      >
        {initials(name)}
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-xs uppercase tracking-[0.2em] text-[var(--color-muted-foreground)]">
          Public profile
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">{name}</h1>
        <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
          @{profile.handle} · joined {formatDate(profile.joined_at)}
        </p>
      </div>
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Stat label="Missions" value={profile.total_missions} />
        <Stat label="Best score" value={profile.best_score ?? "—"} />
        <Stat label="Badges" value={profile.badges.length} />
      </dl>
    </header>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-center">
      <dt className="text-[10px] uppercase tracking-wide text-[var(--color-muted-foreground)]">
        {label}
      </dt>
      <dd className="text-lg font-semibold tracking-tight">{value}</dd>
    </div>
  );
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("");
}
