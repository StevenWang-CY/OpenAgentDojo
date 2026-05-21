/**
 * In-memory user store.
 *
 * Tiny enough that every mission that needs to look at the user shape
 * can read the entire file in a glance. The "fullName" field is the one
 * Mission 09's API-contract-drift scenario renames.
 *
 * Real apps would back this with Postgres; the demo intentionally keeps
 * it in-process so tests are hermetic.
 */

export interface UserRecord {
  id: string;
  role: "user" | "admin";
  /** Free-form long name; may exceed 8 characters. Mission 02 truncates this incorrectly. */
  fullName: string;
  email: string;
  /** Optional avatar URL — added by Mission 10. Reserved here so the type is stable. */
  avatarUrl?: string;
}

const USERS: Record<string, UserRecord> = {
  "u-alice": {
    id: "u-alice",
    role: "user",
    fullName: "Alice Whitfield-Brown",
    email: "alice@example.com",
  },
  "u-bob": {
    id: "u-bob",
    role: "user",
    fullName: "Bob",
    email: "bob@example.com",
  },
  "u-admin": {
    id: "u-admin",
    role: "admin",
    fullName: "Site Admin",
    email: "admin@example.com",
  },
};

export function getUser(id: string): UserRecord | undefined {
  return USERS[id];
}

export function listUsers(): UserRecord[] {
  return Object.values(USERS);
}

export function upsertUser(record: UserRecord): void {
  USERS[record.id] = record;
}

/** Reset the store to its seed state. Test-only. */
export function _resetForTests(): void {
  for (const key of Object.keys(USERS)) {
    delete USERS[key];
  }
  USERS["u-alice"] = {
    id: "u-alice",
    role: "user",
    fullName: "Alice Whitfield-Brown",
    email: "alice@example.com",
  };
  USERS["u-bob"] = {
    id: "u-bob",
    role: "user",
    fullName: "Bob",
    email: "bob@example.com",
  };
  USERS["u-admin"] = {
    id: "u-admin",
    role: "admin",
    fullName: "Site Admin",
    email: "admin@example.com",
  };
}
