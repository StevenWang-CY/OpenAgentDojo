import type { User } from "./types/user";

interface HeaderProps {
  user: User | null;
  onSignOut: () => void;
}

/**
 * Top navigation bar. Renders the current user's name on the right.
 *
 * Mission 09's hidden test asserts that the header renders without
 * throwing when the backend returns the post-rename `displayName`
 * payload. The shipped code reads `user.fullName`, which after the
 * rename is `undefined` — TypeScript would have caught this if the
 * frontend's `User` type tracked the wire shape.
 */
export function Header({ user, onSignOut }: HeaderProps) {
  return (
    <header className="site-header">
      <strong className="site-header__brand">fullstack-auth-demo</strong>
      {user && (
        <span className="site-header__user">
          Signed in as <em>{user.fullName}</em>
          <button type="button" onClick={onSignOut}>
            Sign out
          </button>
        </span>
      )}
    </header>
  );
}
