/**
 * P1-6 — Replay download end-to-end flow.
 *
 * Two scenarios:
 *
 *   1) Owner navigates to a graded report → opens the Share dropdown →
 *      clicks "Download replay (JSON)" → the browser receives a 200
 *      with a valid JSON body that contains the canonical envelope.
 *
 *   2) Same flow but for the ZIP variant → the saved file name matches
 *      the canonical ``arena-replay-{short}-{ymd}.zip`` pattern.
 *
 * The backend is intercepted via Playwright's ``page.route`` so the test
 * runs without a live API. The route handlers return the minimum shape
 * required to (a) load the report and (b) serve a deterministic replay
 * artefact. The test is soft-skipped if the dev server cannot reach the
 * report route.
 */
import { expect, test, type Page, type Route, type Download } from "@playwright/test";

const SUBMISSION_ID = "11111111-2222-3333-4444-555555555555";
const SESSION_ID = "22222222-3333-4444-5555-666666666666";
const MISSION_ID = "auth-cookie-expiration";
const ANY_API_PREFIX = /https?:\/\/[^/]+\/api\/v1/;

// Minimum Submission shape that ReportView will render — mirrors the
// fixture used by the unit test suite. Kept inline so this spec is
// self-contained.
function reportFixture() {
  return {
    id: SUBMISSION_ID,
    session_id: SESSION_ID,
    mission_id: MISSION_ID,
    final_diff: "diff --git a/file b/file\n",
    visible_test_results: [],
    hidden_test_results: [],
    validator_results: [],
    total_score: 78,
    created_at: "2026-05-21T10:00:00Z",
    ideal_solution: "## Reference fix\n",
    ideal_solution_diff: null,
    agent_patch_diff: null,
    critical_moments: [],
    verified: false,
    score_report: {
      total: 78,
      missed_failure_mode: false,
      strengths: ["Picked the right context"],
      weaknesses: ["Did not run typecheck"],
      badges_earned: [],
      dimensions: {
        final_correctness: { score: 24, max: 30, signals: ["3/4 hidden tests pass"] },
        verification: { score: 14, max: 20, signals: [] },
        agent_review: { score: 11, max: 15, signals: [] },
        prompt_quality: { score: 7, max: 10, signals: [] },
        context_selection: { score: 8, max: 10, signals: [] },
        safety: { score: 9, max: 10, signals: [] },
        diff_minimality: { score: 5, max: 5, signals: [] },
      },
    },
  };
}

function replayFixture() {
  return {
    schema_version: 1,
    kind: "openagentdojo.replay.v1",
    submission_id: SUBMISSION_ID,
    envelope: {
      schema_version: 1,
      submission_id: SUBMISSION_ID,
      handle: "jane",
      display_name: "Jane Doe",
      mission_id: MISSION_ID,
      mission_version: 1,
      rubric_version: "v1",
      total_score: 78,
      effective_max: 100,
      missed_failure_mode: false,
      score_cap_reason: null,
      proctored: false,
      attempt_index: 1,
      graded_at: "2026-05-23T18:42:11Z",
    },
    envelope_signature: "0xdeadbeef",
    score_report: {},
    events: [],
    final_diff: "diff --git a/file b/file\n",
    mission_pointer: {
      id: MISSION_ID,
      version: 1,
      manifest_sha256: "abc",
      repo_pack_id: "fullstack-auth-demo",
      repo_pack_sha: "def",
    },
    exported_at: "2026-05-28T12:00:00Z",
    exported_at_omitted_from_signature: true,
    replay_signature: "0xfeedface",
  };
}

async function installReportStubs(page: Page): Promise<void> {
  // GET /me — minimal authenticated user so ReportView treats us as owner
  // and renders the dropdown trigger.
  await page.route(
    new RegExp(`${ANY_API_PREFIX.source}/auth/me$`),
    async (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "00000000-0000-0000-0000-000000000001",
          email: "owner@example.com",
          handle: "owner",
          display_name: "Owner",
          created_at: "2026-01-01T00:00:00Z",
          pending_email: null,
          deletion_scheduled_at: null,
          tutorial_completed: true,
          consent_policy_version: 1,
          coaching_reflections_enabled: true,
          session_epoch: 1,
        }),
      }),
  );

  // GET /reports/{id}
  await page.route(
    new RegExp(`${ANY_API_PREFIX.source}/reports/${SUBMISSION_ID}(\\?.*)?$`),
    async (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(reportFixture()),
      }),
  );

  // GET /sessions/{id}/timeline — empty list keeps the timeline panel quiet.
  await page.route(
    new RegExp(`${ANY_API_PREFIX.source}/sessions/${SESSION_ID}/timeline$`),
    async (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      }),
  );

  // GET /me/recommendations — empty so the report footer doesn't 401.
  await page.route(
    new RegExp(`${ANY_API_PREFIX.source}/me/recommendations$`),
    async (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          generated_at: "2026-05-28T00:00:00Z",
          weakest_dim: null,
          rationale: "",
          recommendations: [],
        }),
      }),
  );
}

test.describe("P1-6 replay export", () => {
  test("downloads a valid JSON artefact from the share dropdown", async ({
    page,
  }) => {
    await installReportStubs(page);

    let jsonCallCount = 0;
    await page.route(
      new RegExp(`${ANY_API_PREFIX.source}/submissions/${SUBMISSION_ID}/replay\\.json(\\?.*)?$`),
      async (route: Route) => {
        jsonCallCount += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          headers: {
            ETag: 'W/"replay-deadbeef"',
            "Cache-Control": "public, max-age=31536000, immutable",
          },
          body: JSON.stringify(replayFixture()),
        });
      },
    );

    // Navigate. If the dev server isn't reachable, soft-skip — the same
    // pattern as the scratchpad-flow spec.
    const response = await page.goto(`/report/${SUBMISSION_ID}`, {
      waitUntil: "domcontentloaded",
    });
    if (!response || !response.ok()) {
      test.skip(true, "dev server unreachable; cannot exercise report route");
      return;
    }

    // Wait for the dropdown trigger to mount.
    const trigger = page.getByTestId("share-dropdown-trigger");
    await trigger.waitFor({ state: "visible", timeout: 15_000 });
    await trigger.click();

    // The JSON variant triggers a programmatic anchor click that
    // ``download`` an in-memory blob — Playwright surfaces this as a
    // Download event.
    const downloadPromise: Promise<Download> = page.waitForEvent("download", {
      timeout: 10_000,
    });
    await page.getByTestId("replay-json-item").click();
    const download = await downloadPromise;

    expect(download.suggestedFilename()).toMatch(
      new RegExp(`arena-replay-${SUBMISSION_ID.slice(0, 8)}\\.json$`),
    );

    // Confirm the BE was actually hit and the payload is the JSON envelope.
    expect(jsonCallCount).toBeGreaterThanOrEqual(1);
    const path = await download.path();
    if (path) {
      const fs = await import("node:fs/promises");
      const raw = await fs.readFile(path, "utf8");
      const parsed = JSON.parse(raw) as { kind?: string; submission_id?: string };
      expect(parsed.kind).toBe("openagentdojo.replay.v1");
      expect(parsed.submission_id).toBe(SUBMISSION_ID);
    }
  });

  test("downloads a zip artefact with the canonical filename pattern", async ({
    page,
  }) => {
    await installReportStubs(page);

    const zipBytes = Buffer.from("PK\x03\x04stub-zip", "binary");
    const cannedFilename = `arena-replay-${SUBMISSION_ID.slice(0, 8)}-20260523.zip`;
    await page.route(
      new RegExp(`${ANY_API_PREFIX.source}/submissions/${SUBMISSION_ID}/replay\\.zip(\\?.*)?$`),
      async (route: Route) =>
        route.fulfill({
          status: 200,
          contentType: "application/zip",
          headers: {
            "Content-Disposition": `attachment; filename="${cannedFilename}"`,
          },
          body: zipBytes,
        }),
    );

    const response = await page.goto(`/report/${SUBMISSION_ID}`, {
      waitUntil: "domcontentloaded",
    });
    if (!response || !response.ok()) {
      test.skip(true, "dev server unreachable; cannot exercise report route");
      return;
    }

    const trigger = page.getByTestId("share-dropdown-trigger");
    await trigger.waitFor({ state: "visible", timeout: 15_000 });
    await trigger.click();

    const downloadPromise: Promise<Download> = page.waitForEvent("download", {
      timeout: 10_000,
    });
    await page.getByTestId("replay-zip-item").click();
    const download = await downloadPromise;

    // Filename pattern: arena-replay-<8 hex>-<8 digit date>.zip
    expect(download.suggestedFilename()).toMatch(
      /^arena-replay-[0-9a-f]{8}-\d{8}\.zip$/,
    );
  });
});
