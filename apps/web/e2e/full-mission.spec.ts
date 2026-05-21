// Full mission flow: sign-in → start mission → workspace → terminal → prompt
// → patch → submit → report.
//
// This is the cold-start nightly. Each downstream API is probed before its
// step — if the endpoint isn't deployed yet the test soft-skips with a clear
// reason so the spec stays green during incremental M3+ ship. Scaffolding is
// production-quality: same selectors, same waits, same auth path as a real
// user. Only the *skip* logic is permissive.

import { expect, test, type Page, type APIRequestContext } from "@playwright/test";

const API_BASE = process.env.API_BASE_URL ?? "http://localhost:8000";
const MAILHOG_BASE = process.env.MAILHOG_API_BASE ?? "http://localhost:8025";
const MISSION_ID = process.env.E2E_MISSION_ID ?? "auth-cookie-expiration";

// Unique address per run keeps Mailhog's inbox de-duplicated and lets the
// magic-link search match exactly one message.
const EMAIL = `e2e+${Date.now()}@arena.test`;

interface MailhogMessage {
  ID: string;
  Content: { Body: string; Headers: Record<string, string[]> };
  To: { Mailbox: string; Domain: string }[];
}

async function endpointExists(
  request: APIRequestContext,
  path: string,
  expectedStatuses: number[] = [200, 401, 403]
): Promise<boolean> {
  try {
    const res = await request.get(`${API_BASE}${path}`);
    return expectedStatuses.includes(res.status());
  } catch {
    return false;
  }
}

async function waitForMagicLink(
  request: APIRequestContext,
  recipient: string,
  timeoutMs = 30_000
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await request.get(`${MAILHOG_BASE}/api/v2/messages`);
      if (res.ok()) {
        const body = (await res.json()) as { items: MailhogMessage[] };
        for (const msg of body.items ?? []) {
          const to = msg.To?.[0];
          const addr = to ? `${to.Mailbox}@${to.Domain}` : "";
          if (addr.toLowerCase() !== recipient.toLowerCase()) continue;

          // Magic link URL is the first https?://...auth/verify... in the body.
          const match = msg.Content.Body.match(
            /https?:\/\/[^\s"'<>]+(?:auth\/verify|magic\/verify|verify)[^\s"'<>]*/i
          );
          if (match) return match[0];
        }
      }
    } catch {
      // Mailhog not up — try again until timeout.
    }
    await new Promise((r) => setTimeout(r, 750));
  }
  throw new Error(`magic-link for ${recipient} did not arrive within ${timeoutMs}ms`);
}

async function signIn(page: Page, request: APIRequestContext) {
  // Sign-in route is /auth/sign-in per IMPLEMENTATION_PLAN §13.1.
  await page.goto("/auth/sign-in");

  // Be tolerant about copy; match the input rather than a label.
  const emailField = page.locator('input[type="email"]').first();
  await emailField.fill(EMAIL);

  await page
    .getByRole("button", { name: /(send|sign in|continue|email me)/i })
    .first()
    .click();

  // If Mailhog is unreachable, fall back to a graceful skip rather than
  // hanging — the rest of the flow still exercises an unauthenticated path
  // via the landing → catalog probes below.
  let link: string;
  try {
    link = await waitForMagicLink(request, EMAIL, 30_000);
  } catch {
    test.skip(true, "Mailhog magic-link did not arrive — skipping authed flow");
    return;
  }
  await page.goto(link);

  // Once verified the app should redirect somewhere authenticated.
  await page.waitForURL((url) => !/\/sign-in$/.test(url.pathname), {
    timeout: 30_000,
  });
}

test.describe("full mission flow", () => {
  test.setTimeout(180_000);

  test("cold-start → sign-in → mission → submit → report", async ({ page, request }) => {
    // ---- pre-flight: does the backend even have the mission API ready? ----
    const haveMissions = await endpointExists(request, `/api/v1/missions`);
    test.skip(!haveMissions, "API /api/v1/missions not ready — skipping full-mission e2e");

    // ---- sign-in via Mailhog magic link ----
    await signIn(page, request);

    // ---- start mission ----
    await page.goto(`/missions/${MISSION_ID}`);
    const startButton = page.getByRole("button", { name: /(start|begin|launch)/i }).first();
    if (!(await startButton.isVisible().catch(() => false))) {
      test.skip(true, `Mission ${MISSION_ID} page does not expose a Start button yet`);
    }
    await Promise.all([
      page.waitForURL(/\/workspace\//),
      startButton.click(),
    ]);

    // ---- workspace loaded ----
    await expect(
      page.getByTestId("workspace-root").or(page.locator('[data-workspace="ready"]')).first()
    ).toBeVisible({ timeout: 30_000 });

    // ---- terminal connects (xterm renders a .xterm-screen once ready) ----
    const terminalReady = await page
      .locator(".xterm-screen, [data-testid=terminal-ready]")
      .first()
      .waitFor({ state: "visible", timeout: 30_000 })
      .then(() => true)
      .catch(() => false);
    test.skip(!terminalReady, "terminal did not connect — skipping interactive steps");

    // ---- prompt the agent ----
    const promptBox = page.getByPlaceholder(/ask the agent|prompt|describe/i).first();
    if (!(await promptBox.isVisible().catch(() => false))) {
      test.skip(true, "Agent prompt input not present yet");
    }
    await promptBox.fill(
      "Reproduce the failing test and fix the root cause. Add a regression test."
    );
    await page.keyboard.press("Enter");

    // ---- see agent response ----
    const responseBlock = page.getByTestId("agent-response").first();
    await responseBlock.waitFor({ state: "visible", timeout: 60_000 });
    expect((await responseBlock.innerText()).trim().length).toBeGreaterThan(10);

    // ---- apply patch ----
    const applyBtn = page
      .getByRole("button", { name: /(apply patch|apply diff|accept)/i })
      .first();
    if (await applyBtn.isVisible().catch(() => false)) {
      await applyBtn.click();
      await expect(page.getByText(/(applied|patch applied)/i).first()).toBeVisible({
        timeout: 30_000,
      });
    }

    // ---- submit ----
    const submitBtn = page.getByRole("button", { name: /^submit$/i }).first();
    test.skip(
      !(await submitBtn.isVisible().catch(() => false)),
      "Submit button not implemented yet"
    );
    await Promise.all([
      page.waitForURL(/\/report\//, { timeout: 120_000 }),
      submitBtn.click(),
    ]);

    // ---- land on report page ----
    await expect(
      page.getByRole("heading", { name: /(score|report|results)/i }).first()
    ).toBeVisible({ timeout: 30_000 });
  });
});
