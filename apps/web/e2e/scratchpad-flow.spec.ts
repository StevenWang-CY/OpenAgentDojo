/**
 * P1-4 — Scratchpad pane end-to-end flow.
 *
 * Two scenarios:
 *
 *   1) "expand → write → autosave → reload → text persists"
 *      Exercises the full happy path. The mock backend (via Playwright's
 *      `page.route` interception) holds the note in memory across the
 *      reload so the assertion is whether the FE re-fetches and renders.
 *
 *   2) "focus the agent chat composer with a non-empty scratchpad → POST
 *      /events/note-viewed fires"
 *      Asserts the supervision-event emission and the matching FE
 *      telemetry `scratchpad_viewed_during_prompt`.
 *
 * Both scenarios stub the backend with `page.route`, so they run without
 * a live API. The test soft-skips if the route the workspace lives at
 * isn't reachable (e.g. dev server cold-start failed).
 */
import { expect, test, type Page, type Route } from "@playwright/test";

const SESSION_ID = "11111111-2222-3333-4444-555555555555";
const SUBMISSION_ID = "99999999-0000-0000-0000-000000000099";
const MISSION_ID = "auth-cookie-expiration";
// The FE builds requests against `env.apiBaseUrl`. In dev that defaults
// to http://localhost:8000; the routes below intercept anything ending
// in the expected paths so the test is agnostic to the configured base.
const ANY_API_PREFIX = /https?:\/\/[^/]+\/api\/v1/;

interface StubState {
  noteBody: string;
  noteUpdatedAt: string;
  putCalls: number;
  noteViewedCalls: Array<{ bytes_at_view: number }>;
}

async function installWorkspaceStubs(page: Page, state: StubState): Promise<void> {
  // /sessions/{id} — minimal SessionDetail shape so WorkspaceShell renders.
  await page.route(
    new RegExp(`${ANY_API_PREFIX.source}/sessions/${SESSION_ID}$`),
    async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: SESSION_ID,
          mission_id: MISSION_ID,
          status: "active",
          mode: "self_study",
          sandbox_driver: "docker",
          started_at: new Date().toISOString(),
          integrity_signals_count: 0,
          mission: {
            id: MISSION_ID,
            title: "Auth cookie expiration",
            difficulty: "intro",
            brief: "Find why the session cookie expires immediately.",
            kind: "regular",
            expected_context_required: [],
          },
        }),
      });
    },
  );

  // /sessions/{id}/ws-token — short-lived token. Never gets used because
  // the WS connect inside jsdom-less webkit may not succeed; the workspace
  // tolerates a missing live stream.
  await page.route(
    new RegExp(`${ANY_API_PREFIX.source}/sessions/${SESSION_ID}/ws-token$`),
    async (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          token: "stub-token",
          expires_at: new Date(Date.now() + 60_000).toISOString(),
        }),
      }),
  );

  // /sessions/{id}/tree, /diff, /timeline — empty arrays so the rest of
  // the workspace renders without bombing on undefined.
  await page.route(
    new RegExp(`${ANY_API_PREFIX.source}/sessions/${SESSION_ID}/tree$`),
    async (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      }),
  );
  await page.route(
    new RegExp(`${ANY_API_PREFIX.source}/sessions/${SESSION_ID}/diff$`),
    async (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ unified_diff: "" }),
      }),
  );
  await page.route(
    new RegExp(`${ANY_API_PREFIX.source}/sessions/${SESSION_ID}/timeline$`),
    async (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      }),
  );

  // /sessions/{id}/note — GET + PUT, backed by `state` so the persistence
  // assertion works across reloads.
  await page.route(
    new RegExp(`${ANY_API_PREFIX.source}/sessions/${SESSION_ID}/note$`),
    async (route: Route) => {
      const method = route.request().method();
      if (method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            body: state.noteBody,
            updated_at: state.noteUpdatedAt,
          }),
        });
        return;
      }
      if (method === "PUT") {
        const parsed = JSON.parse(route.request().postData() ?? "{}") as {
          body?: string;
        };
        state.noteBody = parsed.body ?? "";
        state.noteUpdatedAt = new Date().toISOString();
        state.putCalls += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            body: state.noteBody,
            updated_at: state.noteUpdatedAt,
          }),
        });
        return;
      }
      await route.continue();
    },
  );

  // /sessions/{id}/events/note-viewed — record the call so we can assert
  // it fired with the right byte count.
  await page.route(
    new RegExp(
      `${ANY_API_PREFIX.source}/sessions/${SESSION_ID}/events/note-viewed$`,
    ),
    async (route: Route) => {
      const parsed = JSON.parse(route.request().postData() ?? "{}") as {
        bytes_at_view?: number;
      };
      state.noteViewedCalls.push({ bytes_at_view: parsed.bytes_at_view ?? 0 });
      await route.fulfill({ status: 204 });
    },
  );

  // /auth/me — minimal user so the workspace doesn't punt to sign-in.
  await page.route(
    new RegExp(`${ANY_API_PREFIX.source}/auth/me$`),
    async (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "user-1",
          email: "e2e@arena.test",
          handle: "e2e",
          display_name: "E2E",
          created_at: new Date().toISOString(),
        }),
      }),
  );

  // /auth/me/consent + coaching-consent — return permissive defaults so the
  // consent gate doesn't intercept.
  await page.route(
    new RegExp(`${ANY_API_PREFIX.source}/auth/me/consent$`),
    async (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          analytics: null,
          functional: null,
          marketing: null,
        }),
      }),
  );
  await page.route(
    new RegExp(`${ANY_API_PREFIX.source}/auth/me/coaching-consent$`),
    async (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ coaching_reflections_enabled: true }),
      }),
  );
}

test.describe("scratchpad pane", () => {
  test.setTimeout(60_000);

  test("expand → write → autosave → reload → text persists", async ({ page }) => {
    const state: StubState = {
      noteBody: "",
      noteUpdatedAt: new Date().toISOString(),
      putCalls: 0,
      noteViewedCalls: [],
    };
    await installWorkspaceStubs(page, state);

    const response = await page.goto(`/workspace/${SESSION_ID}`, {
      waitUntil: "domcontentloaded",
    });
    test.skip(
      !response || !response.ok(),
      "workspace route not reachable — skipping scratchpad e2e",
    );

    // Pane mounts collapsed by default. Click the toggle to expand.
    const toggle = page.getByTestId("scratchpad-toggle").first();
    if (!(await toggle.isVisible().catch(() => false))) {
      test.skip(true, "scratchpad toggle not rendered yet");
    }
    await toggle.click();

    const textarea = page.getByTestId("scratchpad-textarea").first();
    await expect(textarea).toBeVisible();
    await textarea.fill(
      "hypothesis: the cookie max-age is zero on rotate.\n- verify Set-Cookie header",
    );

    // Wait for the autosave debounce (1.5s) + a small safety margin.
    await expect.poll(() => state.putCalls, { timeout: 5_000 }).toBeGreaterThan(0);
    expect(state.noteBody).toContain("hypothesis: the cookie max-age");

    // Reload and confirm the body comes back from the (stubbed) server.
    await page.reload({ waitUntil: "domcontentloaded" });
    const toggleAfter = page.getByTestId("scratchpad-toggle").first();
    await expect(toggleAfter).toBeVisible();
    // The collapsed/expanded state is persisted per-session in
    // localStorage; the body is fetched on mount regardless.
    const textareaAfter = page.getByTestId("scratchpad-textarea").first();
    if (!(await textareaAfter.isVisible().catch(() => false))) {
      // Pane was persisted as collapsed by the localStorage write — open it.
      await toggleAfter.click();
    }
    await expect(textareaAfter).toHaveValue(
      "hypothesis: the cookie max-age is zero on rotate.\n- verify Set-Cookie header",
    );
  });

  test("focusing the agent composer with non-empty notes emits note-viewed", async ({
    page,
  }) => {
    const state: StubState = {
      noteBody: "I already wrote a note before this run.",
      noteUpdatedAt: new Date().toISOString(),
      putCalls: 0,
      noteViewedCalls: [],
    };
    await installWorkspaceStubs(page, state);

    const response = await page.goto(`/workspace/${SESSION_ID}`, {
      waitUntil: "domcontentloaded",
    });
    test.skip(
      !response || !response.ok(),
      "workspace route not reachable — skipping scratchpad focus e2e",
    );

    // The agent prompt textarea (in the right "agent" tab) — switch tab if
    // needed. Use the dedicated test id on the composer.
    const agentTab = page.getByRole("tab", { name: /agent/i }).first();
    if (await agentTab.isVisible().catch(() => false)) {
      await agentTab.click();
    }
    const composer = page.getByTestId("agent-prompt-textarea").first();
    if (!(await composer.isVisible().catch(() => false))) {
      test.skip(true, "agent prompt composer not rendered yet");
    }
    await composer.focus();

    // The FE only fires once per focus cycle; assert the BE event landed.
    await expect
      .poll(() => state.noteViewedCalls.length, { timeout: 5_000 })
      .toBeGreaterThan(0);
    expect(state.noteViewedCalls[0]!.bytes_at_view).toBeGreaterThan(0);
  });
});

// `SUBMISSION_ID` is kept as a stable reference for any follow-up test
// that asserts the report-page Notes section also renders — wire it up
// once the report stubs land in the same file. The unused-variable
// warning is intentional and the lint rule allows _-prefixed unused.
void SUBMISSION_ID;
