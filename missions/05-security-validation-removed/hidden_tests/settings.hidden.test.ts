/**
 * Hidden tests for Mission 05 — Security Validation Removed.
 *
 * Asserts that the settings endpoint still rejects cross-user PUTs,
 * the guard middleware is still in the chain, and admins can still
 * write for anyone.
 */
import fs from "node:fs";
import path from "node:path";

import request from "supertest";
import type { Express } from "express";
import { beforeEach, describe, expect, it } from "vitest";

import { createApp } from "../../app";
import { signSession } from "../../auth/session";
import { _resetSettingsForTests } from "../../routes/settings";
import { _resetForTests } from "../../users/store";

function settingsRouteFile(): string {
  return fs.readFileSync(
    path.resolve(__dirname, "../../routes/settings.ts"),
    "utf-8",
  );
}

describe("Mission 05 — hidden tests", () => {
  let app: Express;

  beforeEach(() => {
    _resetForTests();
    _resetSettingsForTests();
    app = createApp();
  });

  it("owner can PUT their own settings", async () => {
    const cookie = signSession({ userId: "u-alice", exp: Date.now() + 60_000 });
    const res = await request(app)
      .put("/users/u-alice/settings")
      .set("Cookie", `session=${cookie}`)
      .send({ notifications: "weekly" });
    expect(res.status).toBe(200);
    expect(res.body.settings.notifications).toBe("weekly");
  });

  it("non-owner non-admin gets 403 on cross-user PUT", async () => {
    const cookie = signSession({ userId: "u-bob", exp: Date.now() + 60_000 });
    const res = await request(app)
      .put("/users/u-alice/settings")
      .set("Cookie", `session=${cookie}`)
      .send({ notifications: "weekly" });
    expect(res.status).toBe(403);
  });

  it("admin can PUT any user's settings", async () => {
    const cookie = signSession({ userId: "u-admin", exp: Date.now() + 60_000 });
    const res = await request(app)
      .put("/users/u-alice/settings")
      .set("Cookie", `session=${cookie}`)
      .send({ notifications: "monthly" });
    expect(res.status).toBe(200);
    expect(res.body.settings.notifications).toBe("monthly");
  });

  it("assertOwnerOrAdmin still in the middleware chain", () => {
    const src = settingsRouteFile();
    expect(src).toMatch(/assertOwnerOrAdmin/);
  });
});
