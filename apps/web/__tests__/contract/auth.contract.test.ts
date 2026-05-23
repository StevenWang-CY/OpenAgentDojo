/**
 * Auth-route contract tests.
 *
 * These exercise the `auth.*` helpers in `lib/api.ts` against MSW-mocked
 * backend responses whose shapes mirror the corresponding Pydantic models.
 * If `apps/api/openapi.json` drifts away from these payloads, the
 * regenerated `packages/shared-types/src/api.gen.ts` will fail TypeScript
 * compilation in this file — caught before runtime.
 */
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import type { User } from "@arena/shared-types";
import { ApiError, auth } from "@/lib/api";
import { API_BASE, expectShape, server, withContractServer } from "./_setup";

const sampleUser: User = {
  id: "00000000-0000-0000-0000-000000000123",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  github_login: null,
  handle: "ada",
  created_at: "2025-01-01T00:00:00Z",
  last_login_at: "2025-05-20T08:00:00Z",
  csrf_token: "csrf-deadbeef",
  tutorial_completed_at: null,
  tutorial_replay_count: 0,
};

withContractServer([
  http.post(`${API_BASE}/api/v1/auth/magic-link`, async ({ request }) => {
    const body = (await request.json()) as { email: string };
    if (typeof body.email !== "string") {
      return HttpResponse.json(
        { detail: "email is required" },
        { status: 422 }
      );
    }
    return new HttpResponse(null, { status: 204 });
  }),
  http.post(`${API_BASE}/api/v1/auth/logout`, () =>
    new HttpResponse(null, { status: 204 })
  ),
  http.get(`${API_BASE}/api/v1/auth/me`, () => HttpResponse.json(sampleUser)),
]);

describe("auth contract", () => {
  it("POSTs to /api/v1/auth/magic-link with a JSON body and resolves on 204", async () => {
    await expect(
      auth.sendMagicLink({ email: "ada@example.com" })
    ).resolves.toBeUndefined();
  });

  it("POSTs to /api/v1/auth/logout and resolves on 204", async () => {
    await expect(auth.logout()).resolves.toBeUndefined();
  });

  it("GETs /api/v1/auth/me and parses the User payload", async () => {
    const user = await auth.me();
    expectShape(user as unknown as Record<string, unknown>, [
      "id",
      "email",
      "display_name",
      "github_login",
      "created_at",
      "last_login_at",
    ]);
    expect(user.email).toBe("ada@example.com");
  });

  it("hits /api/v1/auth/me — NOT the unmounted /api/v1/me alias", async () => {
    // Force the alias endpoint to fail loudly so we'd notice if `auth.me`
    // started hitting it. (Seam: §12.1 sketches the alias; 4.1 doesn't ship it.)
    server.use(
      http.get(`${API_BASE}/api/v1/me`, () =>
        HttpResponse.json({ detail: "should not be called" }, { status: 500 })
      )
    );
    const user = await auth.me();
    expect(user.id).toBe(sampleUser.id);
  });

  it("surfaces backend errors as ApiError with status code", async () => {
    server.use(
      http.post(`${API_BASE}/api/v1/auth/magic-link`, () =>
        HttpResponse.json({ detail: "invalid email" }, { status: 422 })
      )
    );
    await expect(
      auth.sendMagicLink({ email: "not-an-email" })
    ).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
    });
    await expect(
      auth.sendMagicLink({ email: "not-an-email" })
    ).rejects.toBeInstanceOf(ApiError);
  });
});
