/**
 * P1-5 — three-way diff e2e.
 *
 * Loads a graded mission report and exercises the synchronised-scroll +
 * load-bearing-line marker on a real DOM (Chromium under Playwright).
 * Both the API and the report content are probed before any assertion so
 * the spec soft-skips with a clear reason when the backend hasn't yet
 * seeded a graded submission — the same posture as ``full-mission.spec.ts``.
 */
import { expect, test, type APIRequestContext } from "@playwright/test";

const API_BASE = process.env.API_BASE_URL ?? "http://localhost:8000";
const SUBMISSION_ID = process.env.E2E_GRADED_SUBMISSION_ID ?? "";

async function endpointExists(
  request: APIRequestContext,
  path: string,
  expectedStatuses: number[] = [200, 401, 403],
): Promise<boolean> {
  try {
    const res = await request.get(`${API_BASE}${path}`);
    return expectedStatuses.includes(res.status());
  } catch {
    return false;
  }
}

test.describe("Three-way diff (P1-5)", () => {
  test("synced scroll + marker tooltip", async ({ page, request }) => {
    test.skip(!SUBMISSION_ID, "E2E_GRADED_SUBMISSION_ID not configured");

    const ok = await endpointExists(request, `/api/v1/reports/${SUBMISSION_ID}`);
    test.skip(!ok, "reports endpoint not reachable");

    await page.goto(`/reports/${SUBMISSION_ID}`);

    // Wait for the three-way diff container to mount.
    const diff = page.getByTestId("three-way-diff");
    await expect(diff).toBeVisible({ timeout: 15_000 });

    // Skip if the layout collapsed to tabs (the API didn't seed both
    // diffs or the viewport is unexpectedly narrow); the assertions below
    // assume side-by-side.
    const layout = await diff.getAttribute("data-layout");
    test.skip(layout !== "side-by-side", `unexpected layout ${layout}`);

    const userPane = page.getByTestId("three-way-diff-pane-user");
    const idealPane = page.getByTestId("three-way-diff-pane-ideal");
    await expect(userPane).toBeVisible();
    await expect(idealPane).toBeVisible();

    // Scroll the user pane and assert the ideal pane follows.
    await userPane.evaluate((el) => {
      el.scrollTop = 240;
    });

    // The synced-scroll hook fires via rAF; wait until the ideal pane
    // catches up. Poll for up to 2s.
    await expect
      .poll(
        async () => idealPane.evaluate((el) => el.scrollTop),
        { timeout: 2_000 },
      )
      .toBeGreaterThan(0);

    // Hover the first load-bearing marker on the user pane (when present)
    // and assert the tooltip text matches.
    const marker = userPane.getByTestId("load-bearing-marker-user").first();
    if (await marker.count()) {
      await marker.hover();
      await expect(
        page.getByText(/this line is the one the agent got wrong/i),
      ).toBeVisible({ timeout: 2_000 });
    }
  });
});
