import { Router } from "express";

import { parseSessionCookie, signSession } from "../auth/session";

export const loginRouter = Router();

/** How long a freshly-minted session lasts. */
const SESSION_TTL_MS = 60 * 60 * 1000; // 1 hour

loginRouter.post("/login", (req, res) => {
  const userId =
    typeof req.body?.userId === "string" && req.body.userId.length > 0
      ? (req.body.userId as string)
      : "demo-user";

  const cookie = signSession({
    userId,
    exp: Date.now() + SESSION_TTL_MS,
  });

  res.cookie("session", cookie, {
    httpOnly: true,
    sameSite: "lax",
    // secure: true in prod
  });
  res.status(200).json({ ok: true, userId });
});

/**
 * Refresh endpoint: issues a new session cookie if the current one is
 * still valid. Returns 401 + `status: "expired"` otherwise so the client
 * knows to send the user back through the login flow.
 *
 * NOTE: like `/dashboard`, this currently trusts the parsed payload
 * without re-checking expiration — Mission 01's hidden test exercises
 * that gap.
 */
loginRouter.post("/login/refresh", (req, res) => {
  const raw = req.cookies?.session;
  if (!raw) {
    return res.status(401).json({ ok: false, status: "missing" });
  }
  const parsed = parseSessionCookie(raw);
  if (!parsed) {
    return res.status(401).json({ ok: false, status: "invalid" });
  }
  // BUG (Mission 01): we never check `parsed.exp` here, so expired sessions
  // refresh successfully. The hidden test asserts a 401 status:"expired"
  // response when the inbound cookie is past its `exp`.
  const cookie = signSession({
    userId: parsed.userId,
    exp: Date.now() + SESSION_TTL_MS,
  });
  res.cookie("session", cookie, { httpOnly: true, sameSite: "lax" });
  res.status(200).json({ ok: true, userId: parsed.userId, status: "refreshed" });
});
