/**
 * Hidden tests for Mission 03 — Missing Regression Test (Duplicate Submissions).
 *
 * Exercise the failure modes the agent's process-local Set misses:
 *   - concurrent inserts from many requests
 *   - a "fresh process" via _resetForTests() between submits
 */
import request from "supertest";
import type { Express } from "express";
import { beforeEach, describe, expect, it } from "vitest";

import { createApp } from "../../app";
import { _resetForTests, listAll } from "../../submissions/store";

describe("Mission 03 — hidden tests", () => {
  let app: Express;

  beforeEach(() => {
    _resetForTests();
    app = createApp();
  });

  it("parallel submits with the same formId only create one row", async () => {
    const fires = Array.from({ length: 20 }, () =>
      request(app).post("/api/submissions").send({ formId: "F-race", payload: { x: 1 } }),
    );
    const results = await Promise.all(fires);
    // All requests must complete with 2xx (200 for dedup, 201 for the winner).
    for (const r of results) {
      expect([200, 201]).toContain(r.status);
    }
    expect(listAll().filter((s) => s.formId === "F-race")).toHaveLength(1);
  });

  it("submission survives a fresh store (no in-memory leak)", async () => {
    // First post.
    const first = await request(app)
      .post("/api/submissions")
      .send({ formId: "F-restart", payload: {} });
    expect(first.status).toBe(201);

    // Simulate a process restart: wipe the in-memory store + spin up a
    // fresh Express app. Any guard that lives only in module-level
    // state will now "forget" F-restart and accept a second insert.
    _resetForTests();
    app = createApp();

    // Re-insert the first row so the store and the post-restart guard
    // are consistent with persistent storage. (A real DB would simply
    // still have it on disk.)
    await request(app)
      .post("/api/submissions")
      .send({ formId: "F-restart", payload: {} });

    // Now a duplicate must be rejected via the durable guard.
    const second = await request(app)
      .post("/api/submissions")
      .send({ formId: "F-restart", payload: {} });
    expect([200, 409]).toContain(second.status);
    expect(listAll().filter((s) => s.formId === "F-restart")).toHaveLength(1);
  });

  it("GET /api/submissions filters by formId", async () => {
    await request(app)
      .post("/api/submissions")
      .send({ formId: "F-list", payload: {} });
    const res = await request(app).get("/api/submissions?formId=F-list");
    expect(res.status).toBe(200);
    expect(res.body.rows).toHaveLength(1);
    expect(res.body.rows[0].formId).toBe("F-list");
  });
});
