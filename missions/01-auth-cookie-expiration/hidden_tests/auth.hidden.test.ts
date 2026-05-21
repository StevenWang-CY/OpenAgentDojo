/**
 * Hidden tests for Mission 01 — Auth Cookie Expiration.
 *
 * These run inside the sandbox at submit time after being copied into
 * `backend/src/tests/hidden/`. They exercise the failure mode the agent
 * is designed to miss: presence-only checks that don't validate `exp`.
 *
 * Visible tests (`backend/src/tests/integration/auth.test.ts`) only
 * cover missing-cookie + valid-cookie. The four cases below close the
 * gap.
 */
import request from "supertest";
import type { Express } from "express";
import { beforeEach, describe, expect, it } from "vitest";

import { createApp } from "../../app";
import { signSession } from "../../auth/session";

describe("auth — hidden tests", () => {
  let app: Express;

  beforeEach(() => {
    app = createApp();
  });

  it("expired session redirects to /login", async () => {
    const expired = signSession({
      userId: "u-expired",
      exp: Date.now() - 60_000,
    });
    const res = await request(app)
      .get("/dashboard")
      .set("Cookie", `session=${expired}`);
    expect(res.status).toBe(302);
    expect(res.headers.location).toBe("/login");
  });

  it("tampered session is rejected", async () => {
    const cookie = signSession({
      userId: "u-tamper",
      exp: Date.now() + 60_000,
    });
    // Flip the last character of the signature segment.
    const lastChar = cookie.slice(-1);
    const replacement = lastChar === "A" ? "B" : "A";
    const tampered = cookie.slice(0, -1) + replacement;

    const res = await request(app)
      .get("/dashboard")
      .set("Cookie", `session=${tampered}`);
    expect(res.status).toBe(302);
    expect(res.headers.location).toBe("/login");
  });

  it("valid session remains valid", async () => {
    const cookie = signSession({
      userId: "u-valid",
      exp: Date.now() + 60_000,
    });
    const res = await request(app)
      .get("/dashboard")
      .set("Cookie", `session=${cookie}`);
    expect(res.status).toBe(200);
    expect(res.body.userId).toBe("u-valid");
  });

  it("session refresh respects expiration", async () => {
    const expired = signSession({
      userId: "u-refresh",
      exp: Date.now() - 1_000,
    });
    const res = await request(app)
      .post("/login/refresh")
      .set("Cookie", `session=${expired}`);
    expect(res.status).toBe(401);
    expect(res.body.status).toBe("expired");
  });
});
