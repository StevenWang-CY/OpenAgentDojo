// Type shims for third-party packages that ship without (or with incomplete)
// TypeScript definitions. Keep this file *intentional* — every entry should
// have a comment explaining why it's needed.

// gitdiff-parser ships no .d.ts. We use it as a fallback parser only;
// its return shape is loose enough that `unknown` + structural casting
// at the call site is appropriate.
declare module "gitdiff-parser" {
  const parser: { parse(input: string): unknown[] };
  export default parser;
}

// react-diff-view 3.x ships TypeScript, but `style/index.css` is a CSS
// side-effect import that TS doesn't know about by default.
declare module "react-diff-view/style/index.css";

// xterm CSS likewise (loaded dynamically inside the Terminal client component).
declare module "xterm/css/xterm.css";

// Plain `.css` side-effect imports (e.g. `import "./globals.css"`). Next only
// ships ambient declarations for CSS *modules* (`*.module.css`); a bare global
// stylesheet import has no type. TS 6 rejects side-effect imports of untyped
// modules (TS2882), so declare the wildcard here. The more specific
// `*.module.css` declaration from `next` still wins for CSS-module imports.
declare module "*.css";
