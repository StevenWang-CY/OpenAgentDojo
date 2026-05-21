import react from "@vitejs/plugin-react";
/// <reference types="vitest" />
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8787",
      "/login": "http://localhost:8787",
      "/dashboard": "http://localhost:8787",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/tests/setup.ts"],
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
