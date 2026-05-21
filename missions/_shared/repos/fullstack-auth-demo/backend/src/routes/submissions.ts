import { Router } from "express";

import { insertSubmission, findByFormId } from "../submissions/store";

export const submissionsRouter = Router();

/**
 * POST /api/submissions — accept a form submission.
 *
 * Body: `{ formId: string, payload: object }`.
 *
 * BUG (Mission 03): there is no idempotency guard at all. A double-
 * click on the form, two parallel requests, or a server retry will
 * insert two rows with the same `formId`. The fix is a *durable*
 * uniqueness guard — either a DB-level UNIQUE INDEX on `formId` or a
 * client-issued idempotency token recorded server-side. A process-local
 * `Set<string>` is not enough; it dies with the process.
 */
submissionsRouter.post("/api/submissions", (req, res) => {
  const formId = typeof req.body?.formId === "string" ? req.body.formId : "";
  const payload =
    req.body && typeof req.body.payload === "object" && req.body.payload !== null
      ? (req.body.payload as Record<string, unknown>)
      : {};

  if (formId.length === 0) {
    return res.status(400).json({ ok: false, error: "missing_formId" });
  }

  const row = insertSubmission(formId, payload);
  return res.status(201).json({ ok: true, id: row.id, formId: row.formId });
});

/**
 * GET /api/submissions?formId=... — list submissions for one form.
 *
 * Useful for the hidden test suite to detect duplicates after the fact.
 */
submissionsRouter.get("/api/submissions", (req, res) => {
  const formId = typeof req.query.formId === "string" ? req.query.formId : "";
  if (formId.length === 0) {
    return res.status(400).json({ ok: false, error: "missing_formId" });
  }
  const rows = findByFormId(formId);
  return res.status(200).json({ ok: true, rows });
});
