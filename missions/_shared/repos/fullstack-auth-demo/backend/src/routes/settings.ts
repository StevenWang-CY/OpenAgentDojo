import { Router } from "express";

import { assertOwnerOrAdmin } from "../middleware/assertOwnerOrAdmin";
import { getUser, upsertUser } from "../users/store";

export const settingsRouter = Router();

const SETTINGS: Record<string, Record<string, unknown>> = {};

/**
 * PUT /users/:userId/settings — update the user's settings blob.
 *
 * Authorization: owner (`session.userId === :userId`) or any admin.
 * Gated by `assertOwnerOrAdmin`, which has the param-name bug Mission 05
 * targets.
 */
settingsRouter.put("/users/:userId/settings", assertOwnerOrAdmin, (req, res) => {
  const userId = req.params.userId;
  if (!getUser(userId)) {
    return res.status(404).json({ ok: false, error: "user_not_found" });
  }
  const payload =
    req.body && typeof req.body === "object" ? (req.body as Record<string, unknown>) : {};
  SETTINGS[userId] = { ...(SETTINGS[userId] ?? {}), ...payload };

  // Touch the user record so callers can see the change is real.
  const record = getUser(userId);
  if (record) {
    upsertUser({ ...record });
  }
  return res.status(200).json({ ok: true, userId, settings: SETTINGS[userId] });
});

settingsRouter.get("/users/:userId/settings", assertOwnerOrAdmin, (req, res) => {
  const userId = req.params.userId;
  return res.status(200).json({ ok: true, settings: SETTINGS[userId] ?? {} });
});

/** Test-only reset for the in-process settings dict. */
export function _resetSettingsForTests(): void {
  for (const key of Object.keys(SETTINGS)) {
    delete SETTINGS[key];
  }
}
