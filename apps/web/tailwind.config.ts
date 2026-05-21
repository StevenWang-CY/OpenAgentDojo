import type { Config } from "tailwindcss";

/**
 * Tailwind v4 configuration. v4 reads most config from `globals.css` via the
 * `@theme` block, but a TS config is still useful for the `content` array,
 * the `darkMode` strategy, and JS-driven plugins/extensions.
 */
const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx,mdx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
    "./stores/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          '"SF Pro Text"',
          '"SF Pro Display"',
          '"Segoe UI"',
          "system-ui",
          "sans-serif",
        ],
        mono: [
          '"SF Mono"',
          '"JetBrains Mono"',
          '"Fira Code"',
          "ui-monospace",
          "Menlo",
          "Monaco",
          "Consolas",
          '"Liberation Mono"',
          "monospace",
        ],
      },
      // Typographic scale — 12 / 14 / 16 / 18 / 22 / 28 px.
      fontSize: {
        xs: ["12px", { lineHeight: "16px" }],
        sm: ["14px", { lineHeight: "20px" }],
        base: ["16px", { lineHeight: "24px" }],
        lg: ["18px", { lineHeight: "26px" }],
        xl: ["22px", { lineHeight: "28px" }],
        "2xl": ["28px", { lineHeight: "34px", letterSpacing: "-0.02em" }],
      },
      // Radii scale matches IMPLEMENTATION_PLAN §13 — sm 6 / md 10 / lg 14 / xl 22.
      borderRadius: {
        sm: "6px",
        md: "10px",
        lg: "14px",
        xl: "22px",
      },
      transitionTimingFunction: {
        macos: "cubic-bezier(0.2, 0.8, 0.2, 1)",
        "macos-state": "cubic-bezier(0.4, 0, 0.2, 1)",
      },
      transitionDuration: {
        DEFAULT: "180ms",
        micro: "120ms",
        state: "180ms",
        panel: "250ms",
      },
      boxShadow: {
        soft: "0 1px 2px 0 rgb(0 0 0 / 0.04), 0 1px 3px 0 rgb(0 0 0 / 0.06)",
        elevated:
          "0 1px 2px 0 rgb(0 0 0 / 0.04), 0 10px 30px -10px rgb(0 0 0 / 0.18)",
      },
    },
  },
  plugins: [],
};

export default config;
