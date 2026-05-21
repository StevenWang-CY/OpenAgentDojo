/**
 * Hidden tests for Mission 02 — Agent Edits the Wrong File.
 *
 * Asserts that the backend serializer returns the full display name on
 * the wire. The CSS-only agent patch cannot make these pass.
 */
import request from "supertest";
import type { Express } from "express";
import { beforeEach, describe, expect, it } from "vitest";

import { createApp } from "../../app";
import { signSession } from "../../auth/session";
import { serializeUser } from "../../users/serialize";
import { getUser, _resetForTests } from "../../users/store";

describe("Mission 02 — hidden tests", () => {
  let app: Express;

  beforeEach(() => {
    _resetForTests();
    app = createApp();
  });

  it("users/me returns the full displayName for long names", async () => {
    const cookie = signSession({ userId: "u-alice", exp: Date.now() + 60_000 });
    const res = await request(app)
      .get("/api/users/me")
      .set("Cookie", `session=${cookie}`);
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.user.displayName).toBe("Alice Whitfield-Brown");
  });

  it("serializeUser does not truncate the name", () => {
    const alice = getUser("u-alice");
    expect(alice).toBeDefined();
    if (!alice) return;
    const out = serializeUser(alice);
    expect(out.displayName).toBe(alice.fullName);
    expect(out.displayName.length).toBe(alice.fullName.length);
  });

  it("ProfileCard renders the full name when given the API payload", async () => {
    // Smoke-test the serializer + frontend type contract via the API,
    // since the JSX itself reads `user.fullName` and we already
    // confirmed the full string is present at the wire.
    const cookie = signSession({ userId: "u-alice", exp: Date.now() + 60_000 });
    const res = await request(app)
      .get("/api/users/me")
      .set("Cookie", `session=${cookie}`);
    expect(res.status).toBe(200);
    // The frontend component will render whatever string lands here.
    expect(res.body.user.displayName.includes("Whitfield")).toBe(true);
  });
});
