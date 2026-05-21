/**
 * Hidden tests for Mission 10 — Typecheck Ignored.
 *
 * Asserts that:
 *   1. avatarUrl round-trips through PUT/GET on settings.
 *   2. No source file in `backend/src` or `frontend/src` contains
 *      `as any` after the patch.
 *   3. The repo passes `tsc --noEmit` against both packages.
 *
 * Item (3) is exercised by the grader's `pnpm typecheck` invocation;
 * we additionally validate that no `as any` slipped in.
 */
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import request from "supertest";
import type { Express } from "express";
import { beforeEach, describe, expect, it } from "vitest";

import { createApp } from "../../app";
import { signSession } from "../../auth/session";
import { _resetSettingsForTests } from "../../routes/settings";
import { _resetForTests } from "../../users/store";

const WORKSPACE = path.resolve(__dirname, "../../../..");

function walk(dir: string, ext: RegExp, acc: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === "tests") continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, ext, acc);
    else if (ext.test(entry.name)) acc.push(full);
  }
  return acc;
}

describe("Mission 10 — hidden tests", () => {
  let app: Express;

  beforeEach(() => {
    _resetForTests();
    _resetSettingsForTests();
    app = createApp();
  });

  it("PUT settings can set avatarUrl and GET returns it", async () => {
    const cookie = signSession({ userId: "u-alice", exp: Date.now() + 60_000 });
    const put = await request(app)
      .put("/users/u-alice/settings")
      .set("Cookie", `session=${cookie}`)
      .send({ avatarUrl: "https://cdn.example.com/a.png" });
    expect(put.status).toBe(200);

    const me = await request(app)
      .get("/api/users/me")
      .set("Cookie", `session=${cookie}`);
    expect(me.status).toBe(200);
    expect(me.body.user.avatarUrl).toBe("https://cdn.example.com/a.png");
  });

  it("source contains zero `as any` casts", () => {
    const offenders: string[] = [];
    for (const dir of [
      path.join(WORKSPACE, "backend", "src"),
      path.join(WORKSPACE, "frontend", "src"),
    ]) {
      if (!fs.existsSync(dir)) continue;
      for (const file of walk(dir, /\.tsx?$/)) {
        const body = fs.readFileSync(file, "utf-8");
        if (/\bas\s+any\b/.test(body)) {
          offenders.push(path.relative(WORKSPACE, file));
        }
      }
    }
    expect(offenders, `still contains 'as any': ${offenders.join(", ")}`).toEqual([]);
  });

  it("pnpm typecheck exits 0", () => {
    // Run from the backend package; mirrors mission.repo.test_commands.typecheck.
    const result = execSync("pnpm typecheck", {
      cwd: WORKSPACE,
      stdio: "pipe",
      encoding: "utf-8",
    });
    expect(result).toBeDefined();
  });
});
