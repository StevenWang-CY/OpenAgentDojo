/**
 * Frontend user type.
 *
 * Historically the backend returned `fullName`. The serializer was renamed
 * to `displayName` (see `backend/src/users/serialize.ts`) but the frontend
 * components were not all updated — that's Mission 09's contract drift.
 */
export interface User {
  id: string;
  role: "user" | "admin";
  /**
   * Wire field: was `fullName`, now `displayName`. The remaining `fullName`
   * references in the UI are the bug.
   */
  fullName: string;
  email: string;
  avatarUrl?: string;
}
