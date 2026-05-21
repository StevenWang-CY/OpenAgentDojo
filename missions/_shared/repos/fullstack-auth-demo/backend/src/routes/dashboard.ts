import { Router } from "express";

import { requireAuth } from "../middleware/requireAuth";

export const dashboardRouter = Router();

dashboardRouter.get("/dashboard", requireAuth, (req, res) => {
  const userId = req.session?.userId ?? "anonymous";
  res.status(200).json({
    ok: true,
    userId,
    widgets: ["projects", "notifications", "billing"],
  });
});
