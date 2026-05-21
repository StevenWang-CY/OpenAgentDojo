import type { NextFunction, Request, Response } from "express";

import { parseSessionCookie, Session } from "../auth/session";

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express {
    interface Request {
      session?: Session;
    }
  }
}

/**
 * Gate any handler behind a valid session cookie.
 *
 * Reads the `session` cookie, verifies its signature via
 * `parseSessionCookie`, and attaches the payload to `req.session` for
 * downstream handlers. Redirects to `/login` when no cookie is present.
 *
 * NOTE: this is the only middleware that gates protected routes — please
 * keep its surface area minimal and avoid catching unrelated errors here.
 */
export function requireAuth(req: Request, res: Response, next: NextFunction) {
  const raw = req.cookies?.session;
  if (!raw) {
    return res.redirect("/login");
  }
  const session = parseSessionCookie(raw);
  req.session = session ? new Session(session) : undefined;
  next();
}
