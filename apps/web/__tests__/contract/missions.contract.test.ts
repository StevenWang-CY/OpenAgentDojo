/**
 * Mission-route contract tests.
 *
 * Confirms `listMissions` and `getMission` round-trip through the same
 * MissionListItem / MissionDetail schemas the Pydantic models produce.
 */
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import type { Mission, MissionDetail } from "@arena/shared-types";
import { getMission, listMissions } from "@/lib/api";
import { API_BASE, expectShape, withContractServer } from "./_setup";

const card: Mission = {
  id: "auth-cookie-expiration",
  title: "Expired Session Cookie Still Grants Access",
  short_description: "Users with expired session cookies can still access protected routes.",
  difficulty: "intermediate",
  category: "auth",
  estimated_minutes: 35,
  skills_tested: ["auth", "security", "test-writing", "agent-review"],
  failure_mode_id: "checks_presence_not_expiration",
  version: 1,
  published: true,
  kind: "standard",
  // P1-1 — catalog metadata required by the tightened ``Mission`` alias.
  repo_pack_id: "fullstack-auth-demo",
  language: "typescript",
  tags: ["checks_presence_not_expiration", "lang:typescript"],
  status: "shipped",
  target_release_date: null,
};

const detail: MissionDetail = {
  ...card,
  repo_pack: "fullstack-auth-demo",
  initial_commit: "abc123de",
  manifest_sha256: "f".repeat(64),
  brief: "Auth scenario brief…",
  language_runtime: "node20",
  visible_tests: ["valid session can access dashboard"],
  expected_context_required: ["backend/auth/session.ts"],
  expected_context_recommended: ["docs/auth.md"],
  expected_diff_lines_p50: 18,
};

withContractServer([
  http.get(`${API_BASE}/api/v1/missions`, () => HttpResponse.json([card])),
  http.get(`${API_BASE}/api/v1/missions/:id`, ({ params }) => {
    if (params.id !== card.id) {
      return HttpResponse.json({ detail: "not found" }, { status: 404 });
    }
    return HttpResponse.json(detail);
  }),
]);

describe("missions contract", () => {
  it("GETs /missions and parses MissionListItem[]", async () => {
    const items = await listMissions();
    expect(items).toHaveLength(1);
    const first = items[0]!;
    expectShape(first as unknown as Record<string, unknown>, [
      "id",
      "title",
      "difficulty",
      "category",
      "estimated_minutes",
      "failure_mode_id",
      "skills_tested",
      "version",
      "published",
    ]);
    expect(first.skills_tested).toContain("auth");
  });

  it("GETs /missions/{id} and parses MissionDetail", async () => {
    const d = await getMission("auth-cookie-expiration");
    expectShape(d as unknown as Record<string, unknown>, [
      "id",
      "title",
      "brief",
      "repo_pack",
      "initial_commit",
      "manifest_sha256",
      "visible_tests",
      "expected_context_required",
      "expected_context_recommended",
    ]);
    expect(d.expected_diff_lines_p50).toBe(18);
    expect(d.language_runtime).toBe("node20");
  });

  it("URL-encodes the mission id path segment", async () => {
    await expect(getMission("does-not-exist")).rejects.toMatchObject({
      status: 404,
    });
  });
});
