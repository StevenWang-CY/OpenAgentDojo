import type { User } from "./types/user";

interface SettingsProps {
  user: User;
  onSave: (next: Partial<User>) => void;
}

/**
 * Settings panel — lets the user edit their display name. Reads
 * `user.fullName` for the initial value, which is the third place
 * Mission 09's contract drift bites.
 */
export function Settings({ user, onSave }: SettingsProps) {
  return (
    <section aria-label="Settings" className="settings">
      <h2>Settings for {user.fullName}</h2>
      <label>
        Display name
        <input
          type="text"
          defaultValue={user.fullName}
          onChange={(e) => onSave({ fullName: e.target.value })}
        />
      </label>
    </section>
  );
}
