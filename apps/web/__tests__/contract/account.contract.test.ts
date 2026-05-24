/**
 * Account self-service contract tests (P0-5 + P0-6).
 *
 * Locks the wire contracts for the 10 endpoints under ``/api/v1/auth/me/*``
 * that back the consent banner (P0-5) and the account-settings tab (P0-6).
 *
 * Pattern mirrors ``auth.contract.test.ts``: MSW serves a typed fixture so
 * the round-trip exercises (a) the URL + method that ``lib/api.ts`` builds,
 * (b) the JSON-body / query-string the request carries, and (c) the
 * narrowed response shape the callers consume. The TypeScript compile of
 * this file is the first line of defence — every fixture is typed via
 * ``@arena/shared-types`` so a backend schema change that the FE forgot to
 * absorb fails ``pnpm typecheck`` before ``vitest run``.
 *
 * The error-envelope assertions (``{detail: {code: "..."}}``) intentionally
 * match the structured ArenaError / 4xx-with-code bodies the BE emits so a
 * silent envelope regression (e.g. ``code`` becomes ``error_code``) trips
 * the test rather than reaching the toast layer as a generic 4xx.
 */
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import type {
  ConsentRecord,
  ConsentState,
  DataExport,
  DeletionScheduledRead,
  User,
} from "@arena/shared-types";
import { ApiError, account, auth } from "@/lib/api";
import { API_BASE, expectShape, server, withContractServer } from "./_setup";

// ── Shared fixtures ────────────────────────────────────────────────────────
//
// ``baseUser`` is the post-P0-6 ``UserRead`` shape: the new ``pending_email``
// and ``deletion_scheduled_at`` fields are required-but-nullable on the
// wire, so the fixture sets them to ``null`` rather than omitting them.

const userId = "00000000-0000-0000-0000-000000000999";

const baseUser: User = {
  id: userId,
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  github_login: null,
  handle: "ada",
  created_at: "2026-01-01T00:00:00Z",
  last_login_at: "2026-05-20T08:00:00Z",
  csrf_token: "csrf-deadbeef",
  tutorial_completed_at: null,
  tutorial_replay_count: 0,
  pending_email: null,
  deletion_scheduled_at: null,
};

const grantedRecord: ConsentRecord = {
  at: "2026-05-23T10:00:00Z",
  granted: true,
  version: 1,
};

const consentState: ConsentState = {
  analytics: grantedRecord,
  functional: null,
  marketing: null,
};

const exportId = "aaaaaaaa-1111-2222-3333-444444444444";

const queuedExport: DataExport = {
  id: exportId,
  status: "queued",
  requested_at: "2026-05-23T10:00:00Z",
  // Optional fields surface as ``null`` (not absent) once the bundle finishes;
  // queued exports leave them unset.
};

const readyExport: DataExport = {
  id: exportId,
  status: "ready",
  requested_at: "2026-05-23T10:00:00Z",
  ready_at: "2026-05-23T10:05:00Z",
  expires_at: "2026-05-30T10:05:00Z",
  bytes_total: 4096,
  download_url: "https://signed.example.com/exports/abc?sig=…",
};

const scheduledDeletion: DeletionScheduledRead = {
  scheduled_for: "2026-05-30T10:00:00Z",
};

// ── Default handlers (happy path) ──────────────────────────────────────────
//
// Each spec swaps in 4xx variants via ``server.use`` for the error-path
// assertions, mirroring the give-up gate pattern in ``sessions.contract.test``.

withContractServer([
  // P0-5 — consent
  http.get(`${API_BASE}/api/v1/auth/me/consent`, () =>
    HttpResponse.json(consentState),
  ),
  http.post(`${API_BASE}/api/v1/auth/me/consent`, async ({ request }) => {
    const body = (await request.json()) as { kind?: string; granted?: boolean };
    if (
      body.kind !== "analytics" &&
      body.kind !== "functional" &&
      body.kind !== "marketing"
    ) {
      return HttpResponse.json(
        {
          detail: [
            { msg: "invalid kind", type: "value_error", loc: ["body", "kind"] },
          ],
        },
        { status: 422 },
      );
    }
    return new HttpResponse(null, { status: 204 });
  }),

  // P0-6 — profile
  http.patch(`${API_BASE}/api/v1/auth/me`, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    // ``extra='forbid'`` rejects ``handle`` at the schema layer — surface
    // the same 422 envelope FastAPI would emit so the FE error branch is
    // exercised in the test rather than only at runtime.
    if (Object.prototype.hasOwnProperty.call(body, "handle")) {
      return HttpResponse.json(
        {
          detail: [
            {
              msg: "extra fields not permitted",
              type: "value_error.extra",
              loc: ["body", "handle"],
            },
          ],
        },
        { status: 422 },
      );
    }
    const displayName =
      typeof body.display_name === "string" ? body.display_name : null;
    if (typeof displayName === "string" && displayName.length > 120) {
      return HttpResponse.json(
        {
          detail: [
            {
              msg: "ensure this value has at most 120 characters",
              type: "value_error.any_str.max_length",
              loc: ["body", "display_name"],
            },
          ],
        },
        { status: 422 },
      );
    }
    return HttpResponse.json({ ...baseUser, display_name: displayName });
  }),

  // P0-6 — email change request
  http.post(
    `${API_BASE}/api/v1/auth/me/email/change`,
    async ({ request }) => {
      const body = (await request.json()) as { new_email?: string };
      const email = body.new_email;
      if (typeof email !== "string" || !email.includes("@")) {
        return HttpResponse.json(
          {
            detail: [
              {
                msg: "value is not a valid email address",
                type: "value_error.email",
                loc: ["body", "new_email"],
              },
            ],
          },
          { status: 422 },
        );
      }
      if (email === baseUser.email) {
        return HttpResponse.json(
          { detail: { code: "email_unchanged" } },
          { status: 400 },
        );
      }
      if (email === "taken@example.com") {
        return HttpResponse.json(
          { detail: { code: "email_in_use" } },
          { status: 409 },
        );
      }
      return new HttpResponse(null, { status: 204 });
    },
  ),

  // P0-6 — email change confirm
  http.post(
    `${API_BASE}/api/v1/auth/me/email/confirm`,
    async ({ request }) => {
      const body = (await request.json()) as { token?: string };
      const token = body.token;
      if (token === "invalid") {
        return HttpResponse.json(
          { detail: { code: "invalid_token" } },
          { status: 400 },
        );
      }
      if (token === "no-pending") {
        return HttpResponse.json(
          { detail: { code: "no_pending_email" } },
          { status: 409 },
        );
      }
      if (token === "race") {
        return HttpResponse.json(
          { detail: { code: "email_taken_in_flight" } },
          { status: 409 },
        );
      }
      return HttpResponse.json({
        ...baseUser,
        email: "new@example.com",
        pending_email: null,
      });
    },
  ),

  // P0-6 — sign out of all sessions
  http.post(
    `${API_BASE}/api/v1/auth/me/sessions/sign-out-all`,
    () => new HttpResponse(null, { status: 204 }),
  ),

  // P0-6 — data export request + poll
  http.post(`${API_BASE}/api/v1/auth/me/data-export`, () =>
    HttpResponse.json(queuedExport, { status: 202 }),
  ),
  http.get(
    `${API_BASE}/api/v1/auth/me/data-export/:id`,
    ({ params }) => {
      if (params.id !== exportId) {
        return HttpResponse.json({ detail: "not found" }, { status: 404 });
      }
      return HttpResponse.json(readyExport);
    },
  ),

  // P0-6 — delete account (schedule + cancel)
  http.post(`${API_BASE}/api/v1/auth/me/delete`, async ({ request }) => {
    const body = (await request.json()) as { confirm_email?: string };
    if (body.confirm_email !== baseUser.email) {
      return HttpResponse.json(
        { detail: { code: "email_mismatch" } },
        { status: 400 },
      );
    }
    return HttpResponse.json(scheduledDeletion);
  }),
  http.post(
    `${API_BASE}/api/v1/auth/me/delete/cancel`,
    () => new HttpResponse(null, { status: 204 }),
  ),
]);

// ── P0-5 — consent ─────────────────────────────────────────────────────────

describe("auth.getConsent / auth.setConsentRecord contract", () => {
  it("GETs /auth/me/consent and parses ConsentState with nullable per-kind records", async () => {
    const state = await auth.getConsent();
    expectShape(state as unknown as Record<string, unknown>, [
      "analytics",
      "functional",
      "marketing",
    ]);
    expect(state.analytics?.granted).toBe(true);
    expect(state.analytics?.version).toBe(1);
    expect(state.functional).toBeNull();
    expect(state.marketing).toBeNull();
  });

  it("surfaces 401 on the consent GET as ApiError so signed-out callers can no-op", async () => {
    server.use(
      http.get(`${API_BASE}/api/v1/auth/me/consent`, () =>
        HttpResponse.json({ detail: "not authenticated" }, { status: 401 }),
      ),
    );
    await expect(auth.getConsent()).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
    });
  });

  it("POSTs each consent kind and resolves on 204", async () => {
    for (const kind of ["analytics", "functional", "marketing"] as const) {
      await expect(
        auth.setConsentRecord({ kind, granted: true }),
      ).resolves.toBeUndefined();
    }
  });

  it("surfaces 401 on the consent POST as ApiError", async () => {
    server.use(
      http.post(`${API_BASE}/api/v1/auth/me/consent`, () =>
        HttpResponse.json({ detail: "not authenticated" }, { status: 401 }),
      ),
    );
    await expect(
      auth.setConsentRecord({ kind: "analytics", granted: true }),
    ).rejects.toMatchObject({ status: 401 });
  });

  it("surfaces 422 on an invalid kind", async () => {
    await expect(
      // Force the bad value past the type system — the runtime guard on the
      // backend is what we're proving still surfaces a typed 422.
      auth.setConsentRecord({
        kind: "bogus" as unknown as "analytics",
        granted: true,
      }),
    ).rejects.toMatchObject({ status: 422 });
  });
});

// ── P0-6 — profile (display_name) ──────────────────────────────────────────

describe("account.updateProfile contract", () => {
  it("PATCHes /auth/me and parses the extended User envelope (pending_email + deletion_scheduled_at)", async () => {
    const user = await account.updateProfile({ display_name: "Ada Byron" });
    expectShape(user as unknown as Record<string, unknown>, [
      "id",
      "email",
      "display_name",
      "pending_email",
      "deletion_scheduled_at",
      "csrf_token",
      "tutorial_completed_at",
      "tutorial_replay_count",
    ]);
    expect(user.display_name).toBe("Ada Byron");
    expect(user.pending_email).toBeNull();
    expect(user.deletion_scheduled_at).toBeNull();
  });

  it("surfaces 422 when the request body carries ``handle`` (extra='forbid')", async () => {
    // ``handle`` isn't on ``DisplayNameUpdate`` — cast to bypass the FE
    // schema and prove the BE-side guard fires.
    await expect(
      account.updateProfile({
        handle: "ada-byron",
      } as unknown as { display_name?: string | null }),
    ).rejects.toMatchObject({ status: 422 });
  });

  it("surfaces 422 when display_name exceeds 120 chars", async () => {
    await expect(
      account.updateProfile({ display_name: "x".repeat(121) }),
    ).rejects.toMatchObject({ status: 422 });
  });
});

// ── P0-6 — email change (request + confirm) ────────────────────────────────

describe("account.requestEmailChange contract", () => {
  it("POSTs /auth/me/email/change and resolves on 204", async () => {
    await expect(
      account.requestEmailChange({ new_email: "new@example.com" }),
    ).resolves.toBeUndefined();
  });

  it("surfaces 400 ``email_unchanged`` when the new email matches the current", async () => {
    try {
      await account.requestEmailChange({ new_email: baseUser.email });
      throw new Error("expected requestEmailChange to reject with 400");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const api = err as ApiError;
      expect(api.status).toBe(400);
      const detail = api.body?.detail as { code?: string } | undefined;
      expect(detail?.code).toBe("email_unchanged");
    }
  });

  it("surfaces 409 ``email_in_use`` when the address is already taken", async () => {
    try {
      await account.requestEmailChange({ new_email: "taken@example.com" });
      throw new Error("expected requestEmailChange to reject with 409");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const api = err as ApiError;
      expect(api.status).toBe(409);
      expect((api.body?.detail as { code?: string }).code).toBe("email_in_use");
    }
  });

  it("surfaces 422 on an invalid email format", async () => {
    await expect(
      account.requestEmailChange({ new_email: "not-an-email" }),
    ).rejects.toMatchObject({ status: 422 });
  });
});

describe("account.confirmEmailChange contract", () => {
  it("POSTs /auth/me/email/confirm and returns the refreshed User (with new email)", async () => {
    const user = await account.confirmEmailChange({ token: "ok-token" });
    expect(user.email).toBe("new@example.com");
    expect(user.pending_email).toBeNull();
  });

  it("surfaces 400 ``invalid_token`` on a bogus token", async () => {
    try {
      await account.confirmEmailChange({ token: "invalid" });
      throw new Error("expected confirmEmailChange to reject");
    } catch (err) {
      const api = err as ApiError;
      expect(api.status).toBe(400);
      expect((api.body?.detail as { code?: string }).code).toBe(
        "invalid_token",
      );
    }
  });

  it("surfaces 409 ``no_pending_email`` when there's nothing to confirm", async () => {
    try {
      await account.confirmEmailChange({ token: "no-pending" });
      throw new Error("expected confirmEmailChange to reject");
    } catch (err) {
      const api = err as ApiError;
      expect(api.status).toBe(409);
      expect((api.body?.detail as { code?: string }).code).toBe(
        "no_pending_email",
      );
    }
  });

  it("surfaces 409 ``email_taken_in_flight`` for the TOCTOU race", async () => {
    try {
      await account.confirmEmailChange({ token: "race" });
      throw new Error("expected confirmEmailChange to reject");
    } catch (err) {
      const api = err as ApiError;
      expect(api.status).toBe(409);
      expect((api.body?.detail as { code?: string }).code).toBe(
        "email_taken_in_flight",
      );
    }
  });
});

// ── P0-6 — sign out everywhere ─────────────────────────────────────────────

describe("account.signOutAll contract", () => {
  it("POSTs /auth/me/sessions/sign-out-all and resolves on 204", async () => {
    await expect(account.signOutAll()).resolves.toBeUndefined();
  });

  it("surfaces 403 ``deletion_scheduled`` when the account is scheduled for deletion", async () => {
    const scheduledFor = "2026-06-01T00:00:00Z";
    server.use(
      http.post(
        `${API_BASE}/api/v1/auth/me/sessions/sign-out-all`,
        () =>
          HttpResponse.json(
            {
              detail: "Account scheduled for deletion.",
              code: "deletion_scheduled",
              scheduled_for: scheduledFor,
            },
            { status: 403 },
          ),
      ),
    );
    try {
      await account.signOutAll();
      throw new Error("expected signOutAll to reject with 403");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const api = err as ApiError;
      expect(api.status).toBe(403);
      expect(api.body?.code).toBe("deletion_scheduled");
      // ``scheduled_for`` rides along on the same body envelope as ``code``
      // but isn't part of the typed ``ApiErrorBody`` shape — read it
      // through a structural cast so the test stays honest about the wire
      // contract without polluting the FE-facing type.
      expect(
        (api.body as unknown as { scheduled_for?: string } | null)
          ?.scheduled_for,
      ).toBe(scheduledFor);
    }
  });
});

// ── P0-6 — data export (request + poll) ────────────────────────────────────

describe("account.requestExport contract", () => {
  it("POSTs /auth/me/data-export and parses the 202 DataExport envelope", async () => {
    const exp = await account.requestExport();
    expectShape(exp as unknown as Record<string, unknown>, [
      "id",
      "status",
      "requested_at",
    ]);
    expect(exp.status).toBe("queued");
    // ``download_url`` is only populated when ``status==='ready'`` — verify
    // the queued envelope omits it (or sets it null) rather than leaking a
    // half-populated URL.
    expect(exp.download_url ?? null).toBeNull();
  });

  it("surfaces 409 ``export_in_flight`` when a queued/running export exists", async () => {
    server.use(
      http.post(`${API_BASE}/api/v1/auth/me/data-export`, () =>
        HttpResponse.json(
          { detail: { code: "export_in_flight" } },
          { status: 409 },
        ),
      ),
    );
    try {
      await account.requestExport();
      throw new Error("expected requestExport to reject with 409");
    } catch (err) {
      const api = err as ApiError;
      expect(api.status).toBe(409);
      expect((api.body?.detail as { code?: string }).code).toBe(
        "export_in_flight",
      );
    }
  });

  it("surfaces 403 ``deletion_scheduled`` when the account is locked", async () => {
    server.use(
      http.post(`${API_BASE}/api/v1/auth/me/data-export`, () =>
        HttpResponse.json(
          {
            detail: "Account scheduled for deletion.",
            code: "deletion_scheduled",
            scheduled_for: "2026-06-01T00:00:00Z",
          },
          { status: 403 },
        ),
      ),
    );
    await expect(account.requestExport()).rejects.toMatchObject({
      status: 403,
      body: expect.objectContaining({ code: "deletion_scheduled" }),
    });
  });
});

describe("account.getExport contract", () => {
  it("GETs /auth/me/data-export/{id} and parses the DataExport envelope", async () => {
    const exp = await account.getExport(exportId);
    expectShape(exp as unknown as Record<string, unknown>, [
      "id",
      "status",
      "requested_at",
    ]);
    expect(exp.id).toBe(exportId);
  });

  it("populates ``download_url`` only when status === 'ready'", async () => {
    // Default handler returns a ``ready`` fixture.
    const ready = await account.getExport(exportId);
    expect(ready.status).toBe("ready");
    expect(ready.download_url).toBe(readyExport.download_url);

    // Swap to a ``running`` variant and confirm download_url is absent.
    server.use(
      http.get(`${API_BASE}/api/v1/auth/me/data-export/:id`, () =>
        HttpResponse.json({
          ...queuedExport,
          status: "running",
        } satisfies DataExport),
      ),
    );
    const running = await account.getExport(exportId);
    expect(running.status).toBe("running");
    expect(running.download_url ?? null).toBeNull();
  });

  it("surfaces 404 on a non-owned id", async () => {
    server.use(
      http.get(`${API_BASE}/api/v1/auth/me/data-export/:id`, () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
    );
    await expect(account.getExport("not-mine")).rejects.toMatchObject({
      status: 404,
    });
  });
});

// ── P0-6 — schedule + cancel deletion ──────────────────────────────────────

describe("account.scheduleDeletion contract", () => {
  it("POSTs /auth/me/delete with the confirm_email body and parses {scheduled_for}", async () => {
    const res = await account.scheduleDeletion({
      confirm_email: baseUser.email,
    });
    expectShape(res as unknown as Record<string, unknown>, ["scheduled_for"]);
    // The scheduled_for is just an ISO8601 string — confirm shape, not value.
    expect(typeof res.scheduled_for).toBe("string");
    expect(res.scheduled_for).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });

  it("surfaces 400 ``email_mismatch`` when confirm_email is wrong", async () => {
    try {
      await account.scheduleDeletion({ confirm_email: "wrong@example.com" });
      throw new Error("expected scheduleDeletion to reject with 400");
    } catch (err) {
      const api = err as ApiError;
      expect(api.status).toBe(400);
      expect((api.body?.detail as { code?: string }).code).toBe(
        "email_mismatch",
      );
    }
  });
});

describe("account.cancelDeletion contract", () => {
  it("POSTs /auth/me/delete/cancel and resolves on 204", async () => {
    await expect(account.cancelDeletion()).resolves.toBeUndefined();
  });

  it("surfaces 410 ``deletion_already_processed`` once the grace has elapsed", async () => {
    server.use(
      http.post(`${API_BASE}/api/v1/auth/me/delete/cancel`, () =>
        HttpResponse.json(
          { detail: { code: "deletion_already_processed" } },
          { status: 410 },
        ),
      ),
    );
    try {
      await account.cancelDeletion();
      throw new Error("expected cancelDeletion to reject with 410");
    } catch (err) {
      const api = err as ApiError;
      expect(api.status).toBe(410);
      expect((api.body?.detail as { code?: string }).code).toBe(
        "deletion_already_processed",
      );
    }
  });
});
