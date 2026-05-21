import type { UserRecord } from "./store";

/**
 * Serialize a user record for the wire.
 *
 * BUG (Mission 02): the public profile field is truncated to 8 chars
 * with `slice(0, 8)`. The frontend `ProfileCard` shows whatever this
 * returns verbatim, so users with names longer than 8 characters look
 * truncated (e.g. "Alice Whitfield-Brown" -> "Alice Wh"). An agent that
 * only patches the frontend's CSS to add `text-overflow: ellipsis` does
 * not fix the bug — the data is already shortened by the time the
 * browser sees it.
 */
export function serializeUser(record: UserRecord): {
  id: string;
  role: "user" | "admin";
  displayName: string;
  email: string;
  avatarUrl?: string;
} {
  return {
    id: record.id,
    role: record.role,
    // BUG: should be `record.fullName` (no truncation).
    displayName: record.fullName.slice(0, 8),
    email: record.email,
    avatarUrl: record.avatarUrl,
  };
}
