// Flat ESLint config for the Next.js web app.
//
// eslint-config-next 16 ships native flat-config subpath exports
// (`./core-web-vitals`, `./typescript`) and dropped the legacy
// `@rushstack/eslint-patch` shim, so we import them directly instead of
// bridging the old `.eslintrc` presets through `FlatCompat`. This is what
// unblocks ESLint 10 (the patch only supported ESLint 6–9).
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

const eslintConfig = [
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    // Pin the React version so eslint-plugin-react skips runtime version
    // detection. The detection path calls the `context.getFilename()` API that
    // ESLint 10 removed, which otherwise crashes the whole lint run.
    settings: { react: { version: "19.2" } },
  },
  {
    rules: {
      // eslint-config-next 16 bundles eslint-plugin-react-hooks v7, whose
      // "recommended" set newly enables React-Compiler-era rules that the
      // legacy v15 preset never enforced. These fire on long-standing,
      // intentional patterns — `set-state-in-effect` on the canonical
      // "read a browser API (media query / storage) once on mount" sync, and
      // `purity` on `Date.now()` used for display timestamps. Keep this bump a
      // behavior-preserving ESLint 9→10 migration: leave them off here and
      // adopt React-Compiler readiness as a separate, deliberate change.
      // `rules-of-hooks` and `exhaustive-deps` stay enforced.
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/purity": "off",
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "@typescript-eslint/no-explicit-any": "error",
      // Pin explicit options so the rule never destructures an undefined
      // options[0] (the legacy preset used to set this to bare severity `1`).
      "@typescript-eslint/no-unused-expressions": [
        "warn",
        {
          allowShortCircuit: true,
          allowTernary: true,
          allowTaggedTemplates: true,
        },
      ],
    },
  },
  {
    ignores: [".next/**", "node_modules/**", "playwright-report/**", "test-results/**"],
  },
];

export default eslintConfig;
