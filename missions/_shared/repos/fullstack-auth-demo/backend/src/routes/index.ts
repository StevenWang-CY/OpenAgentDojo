import { Router } from "express";

import { dashboardRouter } from "./dashboard";
import { loginRouter } from "./login";
import { settingsRouter } from "./settings";
import { submissionsRouter } from "./submissions";
import { usersRouter } from "./users";

export const apiRouter = Router();

apiRouter.use(loginRouter);
apiRouter.use(dashboardRouter);
apiRouter.use(usersRouter);
apiRouter.use(submissionsRouter);
apiRouter.use(settingsRouter);

apiRouter.get("/healthz", (_req, res) => {
  res.status(200).json({ ok: true });
});
