import { createHmac, timingSafeEqual } from "node:crypto";

/**
 * Session cookie format & semantics.
 *
 * A signed cookie carries `{ userId, exp }` where `exp` is an epoch
 * millisecond timestamp. The on-wire format is:
 *
 *     base64url(JSON(payload)) + "." + base64url(hmacSha256(payload))
 *
 * `parseSessionCookie` returns the payload only when the signature is
 * valid; it does **not** check expiration. Expiration is the job of
 * `Session.isValid(now)`. Both checks must run for the request to be
 * allowed through `requireAuth`.
 */

// Hardcoded HMAC secret. Fine for the demo; rotate via env in a real app.
const SESSION_SECRET = "demo-session-secret-do-not-use-in-prod";

export interface SessionPayload {
  userId: string;
  /** Epoch milliseconds at which this session stops being valid. */
  exp: number;
}

function b64url(buf: Buffer | string): string {
  const b = typeof buf === "string" ? Buffer.from(buf, "utf8") : buf;
  return b
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function b64urlDecode(s: string): Buffer {
  const pad = s.length % 4 === 0 ? "" : "=".repeat(4 - (s.length % 4));
  return Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/") + pad, "base64");
}

function sign(payloadEncoded: string): string {
  return b64url(
    createHmac("sha256", SESSION_SECRET).update(payloadEncoded).digest(),
  );
}

/** Sign a session payload into the on-wire cookie string. */
export function signSession(payload: SessionPayload): string {
  const json = JSON.stringify(payload);
  const payloadEncoded = b64url(json);
  const sig = sign(payloadEncoded);
  return `${payloadEncoded}.${sig}`;
}

/**
 * Verify the signature on a raw cookie value and return the payload, or
 * null if the cookie is malformed, tampered, or has a bad signature.
 *
 * NOTE: this function does NOT check expiration. Callers MUST call
 * `Session.isValid(Date.now())` before trusting the result for access
 * control decisions.
 */
export function parseSessionCookie(raw: string): SessionPayload | null {
  if (typeof raw !== "string" || raw.length === 0) return null;
  const dot = raw.indexOf(".");
  if (dot <= 0 || dot === raw.length - 1) return null;
  const payloadEncoded = raw.slice(0, dot);
  const sigGiven = raw.slice(dot + 1);

  const expected = sign(payloadEncoded);
  const a = Buffer.from(sigGiven);
  const b = Buffer.from(expected);
  if (a.length !== b.length) return null;
  if (!timingSafeEqual(a, b)) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(b64urlDecode(payloadEncoded).toString("utf8"));
  } catch {
    return null;
  }
  if (
    !parsed ||
    typeof parsed !== "object" ||
    typeof (parsed as SessionPayload).userId !== "string" ||
    typeof (parsed as SessionPayload).exp !== "number"
  ) {
    return null;
  }
  return parsed as SessionPayload;
}

/**
 * Thin wrapper around a session payload. The canonical expiration check
 * lives on this class — middleware/handlers should always go through
 * `.isValid(now)` rather than comparing `payload.exp` directly.
 */
export class Session {
  public readonly userId: string;
  public readonly exp: number;

  constructor(payload: SessionPayload) {
    this.userId = payload.userId;
    this.exp = payload.exp;
  }

  /** True when the session has not yet expired. */
  isValid(now: number): boolean {
    return this.exp > now;
  }
}

/**
 * Convenience helper for tests: produce a Session instance directly from
 * a payload (skipping the cookie round-trip).
 */
export function sessionFromPayload(payload: SessionPayload): Session {
  return new Session(payload);
}
