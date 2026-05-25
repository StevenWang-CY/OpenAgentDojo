import type { PublicProfile } from "@arena/shared-types";
import { formatDate } from "@/lib/format";
import { VerificationBadge } from "./VerificationBadge";

interface ProfileHeaderProps {
  profile: PublicProfile;
}

export function ProfileHeader({ profile }: ProfileHeaderProps) {
  const name = profile.display_name ?? profile.handle;
  return (
    <header className="flex flex-col items-start gap-6 border-b border-[var(--color-border)] pb-7 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h1
          id="profile-header"
          className="break-all font-mono text-[44px] font-medium leading-none tracking-tight sm:text-[56px]"
        >
          <span className="text-[var(--color-muted-foreground)]">@</span>
          {profile.handle}
        </h1>
        <p className="mt-3 text-base font-semibold tracking-tight">
          {name === profile.handle ? "—" : name}
        </p>
        {/* P0-7 — verified-via-GitHub chip sits directly below the
            display name so consumers see the identity-trust signal
            without scrolling. The chip itself links to GitHub when
            verified; self-attested rows render the calibration tooltip. */}
        <div className="mt-2">
          <VerificationBadge profile={profile} />
        </div>
        <p className="mt-3 font-mono text-xs text-[var(--color-muted-foreground)]">
          joined {formatDate(profile.joined_at)}
        </p>
      </div>
      <dl className="grid grid-cols-3 items-end gap-9">
        <Stat label="missions" value={profile.total_missions} />
        <Stat label="best score" value={profile.best_score ?? "—"} />
        <Stat label="badges" value={profile.badges.length} />
      </dl>
    </header>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
      <dd className="font-mono text-[28px] font-semibold leading-none tracking-tight tabular-nums">
        {value}
      </dd>
      <dt className="mt-1.5 font-mono text-[10.5px] uppercase tracking-[0.1em] text-[var(--color-muted-foreground)]">
        {label}
      </dt>
    </div>
  );
}
