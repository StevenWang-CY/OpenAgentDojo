import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["__tests__/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules", ".next", "e2e", "playwright-report"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["components/**", "lib/**", "stores/**", "app/**"],
      exclude: ["**/*.d.ts", "**/node_modules/**"],
    },
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "./"),
      "@arena/shared-types": resolve(
        __dirname,
        "../../packages/shared-types/src/index.ts"
      ),
    },
  },
});
