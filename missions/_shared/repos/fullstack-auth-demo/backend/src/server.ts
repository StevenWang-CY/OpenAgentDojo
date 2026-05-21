import { app } from "./app";

const PORT = Number(process.env.PORT ?? 8787);

app.listen(PORT, () => {
  // eslint-disable-next-line no-console
  console.log(`[backend] listening on http://localhost:${PORT}`);
});
