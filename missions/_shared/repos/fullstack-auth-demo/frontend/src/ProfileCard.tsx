import type { User } from "./types/user";

interface ProfileCardProps {
  user: User;
}

/**
 * Profile card — used by Mission 02 (truncated name) and Mission 09
 * (contract drift). Mission 02 patches CSS here; the real fix is in the
 * backend serializer.
 */
export function ProfileCard({ user }: ProfileCardProps) {
  return (
    <article aria-label="Profile card" className="profile-card">
      {user.avatarUrl && (
        <img className="profile-card__avatar" src={user.avatarUrl} alt="" />
      )}
      <h3 className="profile-card__name">{user.fullName}</h3>
      <p className="profile-card__email">{user.email}</p>
      <p className="profile-card__role">{user.role}</p>
    </article>
  );
}
