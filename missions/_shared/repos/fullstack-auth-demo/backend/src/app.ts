import cookieParser from "cookie-parser";
import express from "express";

import { apiRouter } from "./routes";

/**
 * Build a fresh Express app. Exported (not started) so tests can mount
 * it via supertest without binding a TCP port.
 */
export function createApp() {
  const app = express();
  app.use(express.json());
  app.use(cookieParser());
  app.use(apiRouter);
  return app;
}

/** Default app instance used by `server.ts` and integration tests. */
export const app = createApp();
