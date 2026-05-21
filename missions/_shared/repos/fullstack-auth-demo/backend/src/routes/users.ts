import { Router } from "express";

import { parseSessionCookie } from "../auth/session";
import { getUser } from "../users/store";
import { serializeUser } from "../users/serialize";

export const usersRouter = Router();

/**
 * GET /api/users/me — return the current authenticated user's profile.
 *
 * Looks the user up via the signed session cookie. Returns 401 if the
 * cookie is missing or invalid. The response goes through
 * `serializeUser`, which is where Mission 02's truncation bug lives.
 */
usersRouter.get("/api/users/me", (req, res) => {
  const raw = req.cookies?.session;
  if (!raw) {
    return res.status(401).json({ ok: false, error: "missing_session" });
  }
  const parsed = parseSessionCookie(raw);
  if (!parsed) {
    return res.status(401).json({ ok: false, error: "invalid_session" });
  }
  const record = getUser(parsed.userId);
  if (!record) {
    return res.status(404).json({ ok: false, error: "user_not_found" });
  }
  return res.status(200).json({ ok: true, user: serializeUser(record) });
});
