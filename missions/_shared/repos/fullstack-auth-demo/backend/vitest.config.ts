import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    globals: false,
    include: ["src/**/*.test.ts"],
    reporters: ["default"],
    testTimeout: 10_000,
    hookTimeout: 10_000,
  },
});
