import request from "supertest";
import { beforeEach, describe, expect, it } from "vitest";
import type { Express } from "express";

import { createApp } from "../../app";
import { signSession } from "../../auth/session";

describe("auth integration", () => {
  let app: Express;

  beforeEach(() => {
    app = createApp();
  });

  it("missing session redirects to /login", async () => {
    const res = await request(app).get("/dashboard");
    expect(res.status).toBe(302);
    expect(res.headers.location).toBe("/login");
  });

  it("valid session can access /dashboard", async () => {
    const cookie = signSession({
      userId: "u-int",
      exp: Date.now() + 60_000,
    });
    const res = await request(app)
      .get("/dashboard")
      .set("Cookie", `session=${cookie}`);
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.userId).toBe("u-int");
  });

  it("POST /login issues a session cookie", async () => {
    const res = await request(app).post("/login").send({ userId: "u-int" });
    expect(res.status).toBe(200);
    expect(res.body.userId).toBe("u-int");
    const setCookie = res.headers["set-cookie"];
    expect(setCookie).toBeDefined();
    const cookies = Array.isArray(setCookie) ? setCookie : [setCookie];
    expect(cookies.some((c) => c.startsWith("session="))).toBe(true);
  });
});
